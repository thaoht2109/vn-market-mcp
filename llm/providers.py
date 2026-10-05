"""Provider registry + client factory for multi-vendor LLM support.

Priority order (user requirement): Claude first; if ANTHROPIC_API_KEY is
absent, fall back to whichever other provider has a key declared in .env,
in the order models.yaml lists them as fallbacks. This module only answers
"which providers can I actually call right now" and "give me a client for
provider X" — call_role() in llm/client.py still owns the fallback loop and
per-model bookkeeping.

OpenAI, DeepSeek, Grok (xAI) and Ollama all speak the OpenAI-compatible
chat-completions API, so one adapter (_OpenAICompatClient) covers all four;
only base_url/api_key differ. Anthropic keeps its native SDK shape (content
blocks, tool_use, separate `system` param).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


class ProviderNotConfiguredError(Exception):
    """Raised when a provider has no usable API key/base_url in the environment."""


class StructuredChatClient(Protocol):
    """Common interface call_role() drives — one method, one normalized
    return shape, regardless of vendor wire format."""

    def create(
        self, *, model: str, system: str, user_content: str,
        tool_name: str, output_schema: dict, effort: str,
    ) -> "ChatResult": ...


@dataclass
class ChatResult:
    tool_input: dict | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int


@dataclass
class ProviderSpec:
    name: str
    api_key_env: str
    base_url_env: str | None = None
    default_base_url: str | None = None
    requires_key: bool = True


# Order here is only a registry, not the fallback priority — model.yaml's
# per-role `fallback` list decides call order. Grok uses xAI's OpenAI-compatible endpoint.
PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec("anthropic", api_key_env="ANTHROPIC_API_KEY"),
    "openai": ProviderSpec("openai", api_key_env="OPENAI_API_KEY"),
    "deepseek": ProviderSpec(
        "deepseek", api_key_env="DEEPSEEK_API_KEY", base_url_env="DEEPSEEK_BASE_URL",
        default_base_url="https://api.deepseek.com",
    ),
    "grok": ProviderSpec(
        "grok", api_key_env="GROK_API_KEY", base_url_env="GROK_BASE_URL",
        default_base_url="https://api.x.ai/v1",
    ),
    "ollama": ProviderSpec(
        "ollama", api_key_env="", base_url_env="OLLAMA_BASE_URL",
        default_base_url="http://localhost:11434/v1", requires_key=False,
    ),
}


def provider_available(provider_name: str) -> bool:
    spec = PROVIDER_SPECS.get(provider_name)
    if spec is None:
        return False
    if not spec.requires_key:
        return True
    return bool(os.environ.get(spec.api_key_env))


def available_providers() -> list[str]:
    return [name for name in PROVIDER_SPECS if provider_available(name)]


class _AnthropicClient:
    def __init__(self, sdk_client: Any):
        self._client = sdk_client

    def create(self, *, model, system, user_content, tool_name, output_schema, effort) -> ChatResult:
        # cache_control marks the system prompt as a reusable prefix (Anthropic
        # prompt caching, spec §5.7.7) — system prompts here are static per
        # role (SYSTEM_PROMPT constants), so every call after the first within
        # the ~5min TTL reads this block at cache_read price instead of input price.
        response = self._client.messages.create(
            model=model,
            max_tokens=4096,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            tools=[{"name": tool_name, "description": "Emit the structured result.", "input_schema": output_schema}],
            output_config={"effort": effort},
        )
        tool_input = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                tool_input = block.input
                break
        usage = response.usage
        return ChatResult(
            tool_input=tool_input,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cached_tokens=getattr(usage, "cache_read_input_tokens", 0),
        )


def _extract_cached_tokens(usage: Any) -> int:
    if usage is None:
        return 0
    # DeepSeek: top-level usage.prompt_cache_hit_tokens (automatic disk
    # caching, no cache_control needed — spec-equivalent of Anthropic's
    # cache_read, just server-managed). OpenAI/Grok: nested
    # usage.prompt_tokens_details.cached_tokens. Check both; absent on
    # providers/SDK versions that don't report it at all.
    direct = getattr(usage, "prompt_cache_hit_tokens", None)
    if direct is not None:
        return direct
    details = getattr(usage, "prompt_tokens_details", None)
    return getattr(details, "cached_tokens", 0) if details else 0


# Status codes meaning "stop calling this provider until a human acts" —
# 402 (DeepSeek/OpenAI: out of credit) and 429 (rate limit / quota). There's
# no spending-limit API to poll ahead of time (user decision: alert
# reactively off these errors rather than track a budget), so the alert
# fires here, right where the error is first seen.
_BUDGET_EXHAUSTED_STATUS_CODES = {402, 429}


class _OpenAICompatClient:
    """Covers OpenAI, DeepSeek, Grok, Ollama — all implement the same
    chat.completions.create + tool_calls shape. effort is passed as
    reasoning_effort where supported; providers that ignore unknown kwargs
    (DeepSeek/Ollama) simply no-op it rather than erroring."""

    def __init__(self, sdk_client: Any, provider_name: str = ""):
        self._client = sdk_client
        self._provider_name = provider_name

    def create(self, *, model, system, user_content, tool_name, output_schema, effort) -> ChatResult:
        import json as _json

        import openai

        from ops.alerting import send_ops_alert

        kwargs: dict[str, Any] = dict(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_content}],
            tools=[{
                "type": "function",
                "function": {"name": tool_name, "description": "Emit the structured result.", "parameters": output_schema},
            }],
            tool_choice="auto",
        )
        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:
            if exc.status_code in _BUDGET_EXHAUSTED_STATUS_CODES:
                send_ops_alert(
                    f"[vn-market-mcp] Nhà cung cấp LLM {self._provider_name!r} trả lỗi {exc.status_code} "
                    f"(hết hạn mức/rate limit) khi gọi model {model!r} — kiểm tra tài khoản/hạn mức."
                )
            raise
        message = response.choices[0].message
        tool_input = None
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            if call.function.name == tool_name:
                tool_input = _json.loads(call.function.arguments)
                break
        usage = response.usage
        return ChatResult(
            tool_input=tool_input,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            cached_tokens=_extract_cached_tokens(usage),
        )


def make_client(provider_name: str) -> StructuredChatClient:
    spec = PROVIDER_SPECS.get(provider_name)
    if spec is None:
        raise ProviderNotConfiguredError(f"unknown provider {provider_name!r}")
    if not provider_available(provider_name):
        raise ProviderNotConfiguredError(f"provider {provider_name!r} has no {spec.api_key_env} set")

    api_key = os.environ.get(spec.api_key_env) or "not-needed"
    base_url = os.environ.get(spec.base_url_env, spec.default_base_url) if spec.base_url_env else spec.default_base_url

    if provider_name == "anthropic":
        import anthropic

        return _AnthropicClient(anthropic.Anthropic(api_key=api_key))

    import openai

    return _OpenAICompatClient(openai.OpenAI(api_key=api_key, base_url=base_url), provider_name=provider_name)
