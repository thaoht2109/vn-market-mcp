"""Multi-provider LLM role caller (spec §5.7, extended per user requirement
to support ChatGPT/DeepSeek/Grok as fallbacks, not just Claude).

Structured output is implemented as tool/function calling with "auto" tool
choice — Claude 5.x rejects forced tool_choice (spec §5.7.8), so every
provider adapter asks the model to call the tool rather than forcing it.

Provider selection: Claude is always tried first if ANTHROPIC_API_KEY is
set, matching the plan's Claude-primary design. If a role's primary model's
provider has no API key configured, or the call fails, the next model in
RoleConfig.fallback is tried — skipping any whose provider also lacks a key.
Every attempt (including skipped-for-missing-key ones, logged as errors) is
recorded in llm_calls so cost tracking sees the full picture.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from llm.config import ModelsConfig
from llm.providers import ProviderNotConfiguredError, StructuredChatClient, make_client, provider_available


class LLMCallError(Exception):
    """Raised when a role's primary model and all its fallbacks fail or have no configured provider."""


@dataclass
class LLMResult:
    output: dict
    model_id: str
    provider: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    latency_ms: int
    schema_valid: bool


def _load_schema(schema_rel_path: str) -> dict:
    path = Path(__file__).parent.parent / schema_rel_path
    return json.loads(path.read_text())


def _cost_usd(prices: dict, input_tokens: int, output_tokens: int, cached_tokens: int) -> float:
    non_cached_input = max(input_tokens - cached_tokens, 0)
    return (
        non_cached_input * prices["in"] / 1_000_000
        + cached_tokens * prices.get("cache_read", prices["in"]) / 1_000_000
        + output_tokens * prices["out"] / 1_000_000
    )


def call_role(
    cfg: ModelsConfig,
    conn: psycopg.Connection,
    role_name: str,
    system_prompt: str,
    user_content: str,
    run_id: str | None = None,
    client_factory=make_client,
    clients: dict[str, StructuredChatClient] | None = None,
) -> LLMResult:
    """clients: optional {provider_name: StructuredChatClient} override for
    tests — bypasses client_factory/real SDKs entirely, same injection
    pattern as providers/vnstock_provider.py's `clients` dict."""
    role = cfg.role(role_name)
    tool_name = f"emit_{role_name}"
    output_schema = _load_schema(role.output_schema) if role.output_schema else {"type": "object"}

    candidates = [role.model] + role.fallback
    last_error: Exception | None = None

    for model_key in candidates:
        model_id = cfg.model_id(model_key)
        provider_name = cfg.model_provider(model_key)
        started = time.monotonic()
        error_text = None
        tool_input = None
        input_tokens = output_tokens = cached_tokens = 0

        injected = clients.get(provider_name) if clients else None
        if injected is None and not provider_available(provider_name):
            error_text = f"provider {provider_name!r} not configured (missing API key)"
            last_error = ProviderNotConfiguredError(error_text)
        else:
            try:
                client = injected or client_factory(provider_name)
                result = client.create(
                    model=model_id, system=system_prompt, user_content=user_content,
                    tool_name=tool_name, output_schema=output_schema, effort=role.effort,
                )
                tool_input = result.tool_input
                input_tokens, output_tokens, cached_tokens = (
                    result.input_tokens, result.output_tokens, result.cached_tokens,
                )
            except Exception as exc:  # network/API error on this model — try the next fallback
                last_error = exc
                error_text = f"{type(exc).__name__}: {exc}"

        latency_ms = int((time.monotonic() - started) * 1000)
        cost = _cost_usd(cfg.price(model_key), input_tokens, output_tokens, cached_tokens)
        schema_valid = tool_input is not None

        # A savepoint (not conn.commit()) so this log write survives even if
        # the caller's transaction later rolls back for an unrelated reason
        # (spec: never lose a record of money actually spent on a real API
        # call), without forcing a top-level commit of everything else the
        # caller has pending on this connection — a bare conn.commit() here
        # would silently commit the caller's whole transaction (e.g. a
        # run_analysis() call in progress on the same conn), which broke
        # test isolation (tests/test_run_analysis.py) and would do the same
        # in production if a later step in the same conn needs to roll back.
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO llm_calls
                  (run_id, role, model_id, config_hash, prompt_version, input_tokens, output_tokens,
                   cached_tokens, cost_usd, latency_ms, schema_valid, error, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id, role_name, model_id, cfg.config_hash, role.prompt_version,
                    input_tokens, output_tokens, cached_tokens, cost, latency_ms,
                    schema_valid, error_text, datetime.now(timezone.utc),
                ),
            )

        if tool_input is not None:
            return LLMResult(
                output=tool_input, model_id=model_id, provider=provider_name,
                input_tokens=input_tokens, output_tokens=output_tokens, cached_tokens=cached_tokens,
                cost_usd=cost, latency_ms=latency_ms, schema_valid=True,
            )
        if last_error is None:
            # Call succeeded but model didn't call the tool — not a transient
            # error, so don't burn through fallbacks for a prompting issue.
            raise LLMCallError(f"role {role_name!r}: model {model_id!r} did not call tool {tool_name!r}")

    raise LLMCallError(f"role {role_name!r}: no usable provider/model, last error: {last_error}")
