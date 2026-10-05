"""Verifier role (spec §5.7.3): "code đối chiếu số trước; LLM chỉ kiểm tra
kết luận có suy ra được từ bằng chứng không". This module does the code-side
check first (every evidence_ref in both cases must resolve — same rule as
bull_bear.py/synthesis.py) before ever calling the LLM; if that fails, it's a
code/prompt bug, not something an LLM logic check should paper over.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg

from llm.bull_bear import AdvocateResult
from llm.client import LLMResult, call_role
from llm.config import ModelsConfig
from llm.providers import StructuredChatClient
from llm.text_validation import lookup_evidence_ref

ROLE_NAME = "verifier_logic"

SYSTEM_PROMPT = (
    "Bạn là người soát logic. Dưới đây là lập luận Bull và Bear cho cùng một mã, cùng dựa trên một snapshot dữ liệu. "
    "Các số liệu đã được đối chiếu đúng bằng code — nhiệm vụ của bạn CHỈ là kiểm tra: "
    "kết luận của mỗi bên có suy ra hợp lý từ các lập luận đã nêu không (không kiểm tra lại số liệu). "
    "Nếu một lập luận không logic (vd. kết luận không theo sau từ tiền đề), hãy ghi vào issues."
)


class VerifierPrecheckError(Exception):
    """Raised when the code-side evidence_ref check fails — a code/prompt
    bug upstream, not something the LLM logic check should see."""


@dataclass
class VerifierResult:
    bull_logic_valid: bool
    bear_logic_valid: bool
    issues: list[dict]
    llm_meta: LLMResult


def _precheck_evidence_refs(bull: AdvocateResult, bear: AdvocateResult, snapshot: dict) -> None:
    for side_name, result in [("bull", bull), ("bear", bear)]:
        for arg in result.arguments:
            if not lookup_evidence_ref(snapshot, arg["evidence_ref"]):
                raise VerifierPrecheckError(
                    f"{side_name}_advocate evidence_ref {arg['evidence_ref']!r} does not exist in snapshot "
                    "— this should have been caught by bull_bear.py's own validation; verifier only re-checks "
                    "as a second gate before spending an LLM call on logic it can't trust."
                )


def run_verifier_logic(
    conn: psycopg.Connection, cfg: ModelsConfig, snapshot: dict, bull: AdvocateResult, bear: AdvocateResult,
    run_id: str, clients: dict[str, StructuredChatClient] | None = None,
) -> VerifierResult:
    _precheck_evidence_refs(bull, bear, snapshot)

    user_content = (
        "Bull case:\n" + json.dumps({"arguments": bull.arguments, "key_risk": bull.key_risk_to_thesis}, ensure_ascii=False)
        + "\n\nBear case:\n" + json.dumps({"arguments": bear.arguments, "key_risk": bear.key_risk_to_thesis}, ensure_ascii=False)
    )
    result = call_role(cfg, conn, ROLE_NAME, SYSTEM_PROMPT, user_content, run_id=run_id, clients=clients)

    return VerifierResult(
        bull_logic_valid=result.output["bull_logic_valid"],
        bear_logic_valid=result.output["bear_logic_valid"],
        issues=result.output["issues"],
        llm_meta=result,
    )
