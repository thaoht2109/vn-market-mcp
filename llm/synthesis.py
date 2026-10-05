"""Synthesis role (spec §5.7.3): turns a run's snapshot into a thesis with
evidence refs, contradictions, scenarios, and an optional downgrade-only
label recommendation. Code renders the final Markdown from placeholders —
this module only validates the LLM's structured output, it never inserts
numbers into report text itself.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from llm.client import LLMResult, call_role
from llm.config import ModelsConfig
from llm.providers import StructuredChatClient
from llm.text_validation import has_disallowed_digits, lookup_evidence_ref

ROLE_NAME = "synthesis_daily"

SYSTEM_PROMPT = (
    "Bạn là trợ lý phân tích tài chính. Dựa trên dữ liệu snapshot (JSON) được cung cấp, "
    "hãy gọi tool để trả về luận điểm, lập luận hỗ trợ, mâu thuẫn, kịch bản và điều kiện vô hiệu hóa. "
    "QUY TẮC BẮT BUỘC: không viết bất kỳ chữ số nào trong các trường văn bản (trừ năm hoặc số thứ tự mục); "
    "mọi số liệu định lượng phải được tham chiếu qua evidence_ref trỏ tới một trường có thật trong snapshot. "
    "Bạn chỉ được đề nghị HẠ nhãn hành động (downgrade_to), không bao giờ được đề nghị nâng nhãn."
)


class SynthesisValidationError(Exception):
    pass


@dataclass
class SynthesisResult:
    thesis_summary: str
    supporting_points: list[dict]
    contradictions: list[dict]
    scenarios: list[dict]
    invalidation_rules: list[dict]
    downgrade_to: str | None
    downgrade_reason: str | None
    llm_meta: LLMResult


def _validate(output: dict, snapshot: dict) -> None:
    text_fields = [output["thesis_summary"]]
    text_fields += [p["text"] for p in output["supporting_points"]]
    text_fields += [c["text"] for c in output["contradictions"]]
    for text in text_fields:
        if has_disallowed_digits(text):
            raise SynthesisValidationError(f"LLM wrote a digit into free text (not via evidence_ref): {text!r}")

    all_refs = (
        [p["evidence_ref"] for p in output["supporting_points"]]
        + [c["evidence_ref"] for c in output["contradictions"]]
        + [r["evidence_ref"] for r in output["invalidation_rules"]]
    )
    for ref in all_refs:
        if not lookup_evidence_ref(snapshot, ref):
            raise SynthesisValidationError(f"evidence_ref {ref!r} does not exist in snapshot")

    downgrade = output["label_recommendation"].get("downgrade_to")
    if downgrade is not None and downgrade not in {"watch", "reduce_exit", "stay_out"}:
        raise SynthesisValidationError(f"label_recommendation.downgrade_to must be a downgrade label, got {downgrade!r}")


def run_synthesis_daily(
    conn: psycopg.Connection, cfg: ModelsConfig, snapshot: dict, run_id: str,
    clients: dict[str, StructuredChatClient] | None = None,
    role_name: str = ROLE_NAME,
) -> SynthesisResult:
    """role_name: override to "synthesis_full" for the weekly deep-dive cron
    (spec §4.2) — same schema/prompt, higher-effort model per models.yaml."""
    import json

    user_content = (
        "Snapshot dữ liệu (JSON, dùng evidence_ref trỏ vào các trường này):\n"
        + json.dumps(snapshot, ensure_ascii=False, default=str)
    )
    result = call_role(cfg, conn, role_name, SYSTEM_PROMPT, user_content, run_id=run_id, clients=clients)
    _validate(result.output, snapshot)

    return SynthesisResult(
        thesis_summary=result.output["thesis_summary"],
        supporting_points=result.output["supporting_points"],
        contradictions=result.output["contradictions"],
        scenarios=result.output["scenarios"],
        invalidation_rules=result.output["invalidation_rules"],
        downgrade_to=result.output["label_recommendation"].get("downgrade_to"),
        downgrade_reason=result.output["label_recommendation"].get("reason"),
        llm_meta=result,
    )
