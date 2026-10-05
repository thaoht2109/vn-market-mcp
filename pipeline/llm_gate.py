"""Single switch for every LLM role that runs inside the pipeline/worker (news digest,
bull/bear/verifier, synthesis, macro digest). Off by default: the data side only fetches,
validates and stores; the chat LLM (Hermes) is the sole consumer that reasons, per user question.
Flip `llm.pipeline_enabled` in config/vn-rules.yaml to bring the old roles back."""
from __future__ import annotations

from pathlib import Path

import yaml

_RULES = Path(__file__).resolve().parents[1] / "config" / "vn-rules.yaml"


def llm_pipeline_enabled() -> bool:
    return bool(yaml.safe_load(_RULES.read_text()).get("llm", {}).get("pipeline_enabled", False))
