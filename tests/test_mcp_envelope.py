from datetime import datetime, timezone

from mcp_server.envelope import build_envelope


def test_build_envelope_includes_required_fields():
    as_of = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    result = build_envelope({"ticker": "VNM"}, sources=["postgres"], as_of=as_of)
    assert result["as_of"] == "2026-09-30T08:00:00+00:00"
    assert result["sources"] == ["postgres"]
    assert result["data"] == {"ticker": "VNM"}
    assert result["warnings"] == []


def test_build_envelope_defaults_warnings_to_empty_list_not_shared_mutable():
    as_of = datetime(2026, 9, 30, tzinfo=timezone.utc)
    r1 = build_envelope({}, sources=[], as_of=as_of)
    r1["warnings"].append("x")
    r2 = build_envelope({}, sources=[], as_of=as_of)
    assert r2["warnings"] == []


def test_build_envelope_passes_through_explicit_warnings():
    as_of = datetime(2026, 9, 30, tzinfo=timezone.utc)
    result = build_envelope({}, sources=["vnstock"], as_of=as_of, warnings=["dữ liệu cũ"])
    assert result["warnings"] == ["dữ liệu cũ"]
