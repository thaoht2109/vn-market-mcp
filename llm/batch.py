"""Anthropic Batch API support (spec §5.7.1/§5.7.3 backfill note; 50% cheaper
than sync, no completion-time guarantee — spec explicitly says backfill-only,
never for same-day reports that must land before the post-session alert).

Only Claude models support batching in this codebase: DeepSeek (the current
primary model, see config/models.yaml 2026-10-01 cost decision) has no batch
discount at all — its sync price is already lower than Claude's batch price
for most roles — so submit_batch_role() rejects non-Anthropic models rather
than silently running them sync under a misleading "batch" label. Callers
that declare mode: batch but resolve to deepseek should call call_role()
(sync) directly instead; this module is for explicit backfill jobs only.

Flow: submit_batch_role() writes one llm_batch_jobs row + N llm_batch_items
rows and returns immediately (spec: never block a report on batch). A
separate poll (ops job, not built yet — add when a real backfill caller
exists) calls poll_batch_job() periodically to pull results into
llm_batch_items and llm_calls once the provider reports `completed`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from llm.config import ModelsConfig

UNSUPPORTED_BATCH_PROVIDERS = {"deepseek", "grok", "ollama"}  # no batch endpoint at all


class BatchNotSupportedError(Exception):
    pass


@dataclass
class BatchSubmission:
    batch_job_id: int
    provider_batch_id: str
    item_count: int


def _load_schema(schema_rel_path: str) -> dict:
    path = Path(__file__).parent.parent / schema_rel_path
    return json.loads(path.read_text())


def submit_batch_role(
    anthropic_client: Any,
    conn: psycopg.Connection,
    cfg: ModelsConfig,
    role_name: str,
    system_prompt: str,
    items: list[tuple[str, str, str | None]],  # (custom_id, user_content, run_id)
    model_key: str | None = None,
) -> BatchSubmission:
    """items: one (custom_id, user_content, run_id) per ticker/document to
    process in this batch. custom_id must be unique within the batch — the
    provider echoes it back on each result so poll_batch_job can join it to
    llm_batch_items.

    model_key: which models.yaml model to batch under — defaults to the
    role's primary model, but that's deepseek for every role in this phase
    (2026-10-01 cost decision), which has no batch API. Callers that want an
    actual batch discount must pass one of the role's Claude fallbacks
    explicitly, e.g. model_key="sonnet"."""
    role = cfg.role(role_name)
    model_key = model_key or role.model
    model_provider = cfg.model_provider(model_key)
    if model_provider in UNSUPPORTED_BATCH_PROVIDERS:
        raise BatchNotSupportedError(
            f"model {model_key!r} (provider {model_provider!r}) has no batch API — "
            "call llm.client.call_role() (sync) instead, or pass model_key='sonnet'/'opus'/'haiku'"
        )
    if model_provider != "anthropic":
        raise BatchNotSupportedError(f"batch submission only implemented for anthropic, got {model_provider!r}")

    tool_name = f"emit_{role_name}"
    output_schema = _load_schema(role.output_schema) if role.output_schema else {"type": "object"}

    requests = [
        {
            "custom_id": custom_id,
            "params": {
                "model": cfg.model_id(model_key),
                "max_tokens": 4096,
                "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user_content}],
                "tools": [{"name": tool_name, "description": "Emit the structured result.", "input_schema": output_schema}],
                "output_config": {"effort": role.effort},
            },
        }
        for custom_id, user_content, _run_id in items
    ]

    batch = anthropic_client.messages.batches.create(requests=requests)

    row = conn.execute(
        """
        INSERT INTO llm_batch_jobs (provider_batch_id, provider, role, model_key, config_hash, status, item_count)
        VALUES (%s, 'anthropic', %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (batch.id, role_name, model_key, cfg.config_hash, batch.processing_status, len(items)),
    ).fetchone()
    batch_job_id = row[0]

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO llm_batch_items (batch_job_id, custom_id, run_id) VALUES (%s, %s, %s)",
            [(batch_job_id, custom_id, run_id) for custom_id, _content, run_id in items],
        )
    conn.commit()

    return BatchSubmission(batch_job_id=batch_job_id, provider_batch_id=batch.id, item_count=len(items))


def poll_batch_job(anthropic_client: Any, conn: psycopg.Connection, cfg: ModelsConfig, batch_job_id: int) -> str:
    """Checks the provider for status; if completed, pulls per-item results
    into llm_batch_items and logs one llm_calls row per item. Returns the
    current status string. Safe to call repeatedly (idempotent once completed
    — re-fetching an already-completed batch just re-reads the same rows,
    but this function only writes results the first time status flips to
    completed, guarded by the UPDATE...WHERE status != 'completed' below)."""
    row = conn.execute(
        "SELECT provider_batch_id, role, model_key, status FROM llm_batch_jobs WHERE id = %s", (batch_job_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no llm_batch_jobs row with id={batch_job_id}")
    provider_batch_id, role_name, model_key, current_status = row

    batch = anthropic_client.messages.batches.retrieve(provider_batch_id)
    new_status = batch.processing_status

    if new_status != current_status:
        conn.execute("UPDATE llm_batch_jobs SET status = %s WHERE id = %s", (new_status, batch_job_id))
        conn.commit()

    if new_status != "ended" or current_status == "ended":
        return new_status

    role = cfg.role(role_name)
    results = anthropic_client.messages.batches.results(provider_batch_id)
    for entry in results:
        custom_id = entry.custom_id
        if entry.result.type == "succeeded":
            message = entry.result.message
            tool_input = None
            for block in message.content:
                if getattr(block, "type", None) == "tool_use":
                    tool_input = block.input
                    break
            conn.execute(
                "UPDATE llm_batch_items SET status = 'succeeded', output = %s WHERE batch_job_id = %s AND custom_id = %s",
                (json.dumps(tool_input), batch_job_id, custom_id),
            )
            usage = message.usage
            run_id_row = conn.execute(
                "SELECT run_id FROM llm_batch_items WHERE batch_job_id = %s AND custom_id = %s",
                (batch_job_id, custom_id),
            ).fetchone()
            cost = (
                getattr(usage, "input_tokens", 0) * cfg.price(model_key)["in"] / 1_000_000
                + getattr(usage, "output_tokens", 0) * cfg.price(model_key)["out"] / 1_000_000
            ) * 0.5  # batch discount
            conn.execute(
                """
                INSERT INTO llm_calls (run_id, role, model_id, config_hash, prompt_version,
                  input_tokens, output_tokens, cached_tokens, cost_usd, schema_valid, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (
                    run_id_row[0] if run_id_row else None, role_name, cfg.model_id(model_key), cfg.config_hash,
                    role.prompt_version, getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0),
                    getattr(usage, "cache_read_input_tokens", 0), cost, tool_input is not None,
                ),
            )
        else:
            error_text = f"{entry.result.type}: {getattr(entry.result, 'error', '')}"
            conn.execute(
                "UPDATE llm_batch_items SET status = 'errored', error = %s WHERE batch_job_id = %s AND custom_id = %s",
                (error_text, batch_job_id, custom_id),
            )

    conn.execute("UPDATE llm_batch_jobs SET completed_at = now() WHERE id = %s", (batch_job_id,))
    conn.commit()
    return new_status
