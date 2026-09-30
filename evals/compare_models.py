from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

GOLDEN_PATH = Path(__file__).parent / "golden_news.jsonl"

EVENT_TYPES = {"earnings", "dividend", "management_change", "regulatory", "macro", "other"}
SENTIMENTS = {"positive", "neutral", "negative"}


def load_golden_set(path: Path = GOLDEN_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def build_prompt(item: dict) -> str:
    return (
        "Phân loại tin tức chứng khoán sau. Trả về JSON với 2 trường: "
        f"event_type (một trong {sorted(EVENT_TYPES)}) và sentiment (một trong {sorted(SENTIMENTS)}).\n\n"
        f"Mã: {item['ticker']}\nTiêu đề: {item['headline']}\nNội dung: {item['body_excerpt']}"
    )


class SchemaError(Exception):
    pass


def parse_response(raw_json: str) -> dict:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"invalid JSON: {exc}") from exc

    if data.get("event_type") not in EVENT_TYPES:
        raise SchemaError(f"invalid event_type: {data.get('event_type')!r}")
    if data.get("sentiment") not in SENTIMENTS:
        raise SchemaError(f"invalid sentiment: {data.get('sentiment')!r}")
    return data


def score_prediction(expected: dict, predicted: dict) -> bool:
    return (
        expected["expected_event_type"] == predicted["event_type"]
        and expected["expected_sentiment"] == predicted["sentiment"]
    )


@dataclass
class ModelSummary:
    model_id: str
    n: int
    correct: int
    schema_invalid: int
    total_cost_usd: float
    avg_latency_ms: float

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0


def summarize(model_id: str, results: list[dict]) -> ModelSummary:
    n = len(results)
    correct = sum(1 for r in results if r.get("correct"))
    schema_invalid = sum(1 for r in results if r.get("schema_invalid"))
    total_cost = sum(r.get("cost_usd", 0.0) for r in results)
    avg_latency = sum(r.get("latency_ms", 0.0) for r in results) / n if n else 0.0
    return ModelSummary(
        model_id=model_id, n=n, correct=correct, schema_invalid=schema_invalid,
        total_cost_usd=total_cost, avg_latency_ms=avg_latency,
    )


# provider -> (env var giữ base_url, giá trị mặc định nếu env var trống)
# ponytail: chỉ hỗ trợ provider OpenAI-compatible; provider khác (Gemini, Grok...) báo "chưa hỗ trợ"
OPENAI_COMPATIBLE_PROVIDERS = {
    "deepseek": ("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    "ollama": ("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
}

SUPPORTED_PROVIDERS = {"anthropic", *OPENAI_COMPATIBLE_PROVIDERS}


class UnsupportedProviderError(Exception):
    pass


def _call_anthropic(client, model_id: str, prompt: str) -> str:
    response = client.messages.create(
        model=model_id, max_tokens=200, messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def _call_openai_compatible(client, model_id: str, prompt: str) -> str:
    response = client.chat.completions.create(
        model=model_id, max_tokens=200, messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


def _make_client(provider: str):
    if provider == "anthropic":
        import anthropic

        return anthropic.Anthropic()
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        import os

        import openai

        env_var, default_url = OPENAI_COMPATIBLE_PROVIDERS[provider]
        base_url = os.environ.get(env_var, default_url)
        api_key = os.environ.get(f"{provider.upper()}_API_KEY", "ollama")  # ollama không cần key thật
        return openai.OpenAI(base_url=base_url, api_key=api_key)
    raise UnsupportedProviderError(
        f"provider '{provider}' chua duoc ho tro. Provider ho tro: {sorted(SUPPORTED_PROVIDERS)}"
    )


def _call_model(client, provider: str, model_id: str, prompt: str) -> tuple[str, float]:
    start = time.time()
    if provider == "anthropic":
        raw_text = _call_anthropic(client, model_id, prompt)
    elif provider in OPENAI_COMPATIBLE_PROVIDERS:
        raw_text = _call_openai_compatible(client, model_id, prompt)
    else:
        raise UnsupportedProviderError(
            f"provider '{provider}' chua duoc ho tro. Provider ho tro: {sorted(SUPPORTED_PROVIDERS)}"
        )
    latency_ms = (time.time() - start) * 1000
    return raw_text, latency_ms


def main() -> None:
    golden = load_golden_set()
    # (provider, model_id) - them dong moi de test model/provider khac
    targets = [
        ("anthropic", "claude-haiku-4-5-20251001"),
        ("anthropic", "claude-sonnet-5-5"),
        ("anthropic", "claude-opus-5-5"),
        ("deepseek", "deepseek-chat"),
        ("ollama", "llama3.1"),
    ]

    clients: dict[str, object] = {}
    for provider, model_id in targets:
        if provider not in clients:
            clients[provider] = _make_client(provider)
        client = clients[provider]
        results = []
        for item in golden:
            prompt = build_prompt(item)
            raw_text, latency_ms = _call_model(client, provider, model_id, prompt)
            entry = {"latency_ms": latency_ms, "cost_usd": 0.0}
            try:
                predicted = parse_response(raw_text)
                entry["correct"] = score_prediction(item, predicted)
                entry["schema_invalid"] = False
            except SchemaError:
                entry["correct"] = False
                entry["schema_invalid"] = True
            results.append(entry)

        summary = summarize(model_id, results)
        print(
            f"[{provider}] {model_id}: accuracy={summary.accuracy:.0%} "
            f"schema_invalid={summary.schema_invalid}/{summary.n} avg_latency={summary.avg_latency_ms:.0f}ms"
        )


if __name__ == "__main__":
    main()
