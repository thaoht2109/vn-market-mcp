from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any


def serialize_snapshot(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: serialize_snapshot(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: serialize_snapshot(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize_snapshot(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None  # jsonb has no NaN/Infinity; a file would have kept the invalid literal
    return obj
