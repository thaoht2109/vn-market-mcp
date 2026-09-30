from __future__ import annotations

from datetime import datetime


def build_envelope(
    data: dict, sources: list[str], as_of: datetime, warnings: list[str] | None = None
) -> dict:
    return {
        "as_of": as_of.isoformat(),
        "sources": sources,
        "data": data,
        "warnings": list(warnings) if warnings else [],
    }
