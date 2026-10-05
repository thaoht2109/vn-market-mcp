"""Renders Synthesis's placeholder text into the final report string (spec
§5.7.3: "code render ra Markdown" — the LLM never writes numbers itself, it
only references snapshot fields via {{dotted.path}}, so code is the only
thing allowed to fill them in.
"""
from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"\{\{([^}]+)\}\}")


def _resolve(snapshot: dict, ref: str):
    node = snapshot
    for part in ref.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _format_value(value) -> str:
    # Round floats to 2dp for a readable report — raw snapshot values carry
    # full float precision (e.g. composite_score=69.30846223839855) that's
    # noise to a reader, not a meaningful digit.
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_placeholders(text: str, snapshot: dict) -> str:
    def _sub(match: re.Match) -> str:
        value = _resolve(snapshot, match.group(1))
        return "?" if value is None else _format_value(value)

    return _PLACEHOLDER.sub(_sub, text)


def render_synthesis_report(snapshot: dict) -> str | None:
    """Minimal §5.5-style text: thesis summary + supporting points, numbers
    filled from the snapshot. Returns None when there's no synthesis to
    report (role skipped/failed upstream)."""
    synthesis = snapshot.get("synthesis")
    if not synthesis:
        return None

    lines = [render_placeholders(synthesis["thesis_summary"], snapshot)]
    if synthesis["supporting_points"]:
        lines.append("")
        lines.append("Luận điểm hỗ trợ:")
        lines.extend(f"- {render_placeholders(p['text'], snapshot)}" for p in synthesis["supporting_points"])
    if synthesis["contradictions"]:
        lines.append("")
        lines.append("Mâu thuẫn:")
        lines.extend(f"- {render_placeholders(c['text'], snapshot)}" for c in synthesis["contradictions"])
    return "\n".join(lines)
