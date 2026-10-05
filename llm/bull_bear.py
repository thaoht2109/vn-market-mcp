"""Bull/Bear advocate roles (spec §5.7.3): two independent calls over the
same snapshot with opposing system prompts; Synthesis is the arbiter, not
this module. Same evidence_ref/no-digit rules as synthesis.py apply here.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from llm.client import LLMResult, call_role
from llm.config import ModelsConfig
from llm.providers import StructuredChatClient
from llm.text_validation import has_disallowed_digits, lookup_evidence_ref

_SHARED_RULES = (
    "QUY TẮC BẮT BUỘC: không viết bất kỳ chữ số nào trong các trường văn bản (trừ năm); "
    "mọi số liệu định lượng phải được tham chiếu qua evidence_ref trỏ tới một trường có thật trong snapshot. "
    "Chỉ dùng dữ liệu trong snapshot được cung cấp, không suy diễn thêm ngoài dữ liệu."
)

BULL_SYSTEM_PROMPT = (
    "Bạn là nhà phân tích lạc quan (Bull). Dựa trên snapshot dữ liệu, hãy đưa ra lập luận ỦNG HỘ mua/nắm giữ mã này "
    "mạnh nhất có thể, chỉ dùng bằng chứng có thật trong dữ liệu. Đồng thời nêu rủi ro lớn nhất khiến lập luận này có thể sai. "
    + _SHARED_RULES
)

BEAR_SYSTEM_PROMPT = (
    "Bạn là nhà phân tích thận trọng (Bear). Dựa trên snapshot dữ liệu, hãy đưa ra lập luận PHẢN ĐỐI mua/nắm giữ mã này "
    "mạnh nhất có thể, chỉ dùng bằng chứng có thật trong dữ liệu. Đồng thời nêu rủi ro lớn nhất khiến lập luận này có thể sai. "
    + _SHARED_RULES
)


class BullBearValidationError(Exception):
    pass


@dataclass
class AdvocateResult:
    arguments: list[dict]
    key_risk_to_thesis: str
    llm_meta: LLMResult


def _validate(output: dict, snapshot: dict, role_name: str, error_cls: type[Exception]) -> None:
    text_fields = [a["text"] for a in output["arguments"]] + [output["key_risk_to_thesis"]]
    for text in text_fields:
        if has_disallowed_digits(text):
            raise error_cls(f"{role_name}: LLM wrote a digit into free text (not via evidence_ref): {text!r}")

    for arg in output["arguments"]:
        if not lookup_evidence_ref(snapshot, arg["evidence_ref"]):
            raise error_cls(f"{role_name}: evidence_ref {arg['evidence_ref']!r} does not exist in snapshot")


def _run_advocate(
    role_name: str, system_prompt: str,
    conn: psycopg.Connection, cfg: ModelsConfig, snapshot: dict, run_id: str,
    clients: dict[str, StructuredChatClient] | None,
) -> AdvocateResult:
    import json

    user_content = "Snapshot dữ liệu (JSON, dùng evidence_ref trỏ vào các trường này):\n" + json.dumps(
        snapshot, ensure_ascii=False, default=str
    )
    result = call_role(cfg, conn, role_name, system_prompt, user_content, run_id=run_id, clients=clients)
    _validate(result.output, snapshot, role_name, BullBearValidationError)
    return AdvocateResult(
        arguments=result.output["arguments"], key_risk_to_thesis=result.output["key_risk_to_thesis"], llm_meta=result,
    )


def run_bull_case(
    conn: psycopg.Connection, cfg: ModelsConfig, snapshot: dict, run_id: str,
    clients: dict[str, StructuredChatClient] | None = None,
) -> AdvocateResult:
    return _run_advocate("bull_advocate", BULL_SYSTEM_PROMPT, conn, cfg, snapshot, run_id, clients)


def run_bear_case(
    conn: psycopg.Connection, cfg: ModelsConfig, snapshot: dict, run_id: str,
    clients: dict[str, StructuredChatClient] | None = None,
) -> AdvocateResult:
    return _run_advocate("bear_advocate", BEAR_SYSTEM_PROMPT, conn, cfg, snapshot, run_id, clients)
