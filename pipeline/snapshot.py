from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
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
    return obj


def write_snapshot(snapshot_dir: Path, run_id: str, snapshot: dict) -> str:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    safe_name = run_id.replace(":", "_")
    path = Path(snapshot_dir) / f"{safe_name}.json"
    path.write_text(json.dumps(serialize_snapshot(snapshot), indent=2, ensure_ascii=False))
    return str(path)
