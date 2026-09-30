import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.indicators import TechnicalSnapshot
from pipeline.snapshot import write_snapshot


def test_write_snapshot_serializes_dataclasses_and_returns_path(tmp_path):
    tech = TechnicalSnapshot(
        trend="up", ma20=1.0, ma50=1.0, ma200=None, rsi14=55.0, macd=0.1, macd_signal=0.05,
        bollinger_upper=2.0, bollinger_lower=0.5, atr14=0.2, support=[1.0], resistance=[2.0],
        volume_avg20=1000.0, volume_anomaly=False,
    )
    snapshot = {"ticker": "VNM", "as_of": datetime.now(timezone.utc), "technical": tech}

    ref = write_snapshot(tmp_path, "on_demand:VNM:123", snapshot)

    assert Path(ref).exists()
    data = json.loads(Path(ref).read_text())
    assert data["ticker"] == "VNM"
    assert data["technical"]["trend"] == "up"
