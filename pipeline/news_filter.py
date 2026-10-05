"""Tier-1 news filter: deterministic keyword / ticker rules, no LLM.

Never deletes: every item is stored with its verdict (`kept`/`dropped` + reason), so a rule change can be
re-applied to stored items with `python -m pipeline.news_filter --refilter --days N`. When in doubt, keep.
"""
from __future__ import annotations

import argparse
import difflib
import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import yaml

_CONFIG = Path(__file__).parent.parent / "config"
DUPLICATE_RATIO = 0.9
DUPLICATE_WINDOW = timedelta(hours=48)
_TICKER = re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}(?![A-Za-z0-9])")


@dataclass(frozen=True)
class FilterResult:
    status: str            # 'kept' | 'dropped'
    reason: str | None     # why dropped; None when kept
    pillars: list[str]
    tickers: list[str]


def load_keywords(path: Path = _CONFIG / "macro_keywords.yaml") -> dict[str, list[str]]:
    return yaml.safe_load(path.read_text())


def load_aliases(path: Path = _CONFIG / "ticker_aliases.yaml") -> dict[str, list[str]]:
    return yaml.safe_load(path.read_text()) or {}


def _nfc(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "")


def _has(lower_text: str, phrase: str) -> bool:
    """Whole-word match on NFC-normalised, lower-cased text."""
    return re.search(rf"(?<!\w){re.escape(_nfc(phrase).lower())}(?!\w)", lower_text) is not None


def classify(title: str, summary: str | None, stream: str, vn30: set[str],
             aliases: dict[str, list[str]], keywords: dict[str, list[str]]) -> FilterResult:
    text = _nfc(f"{title}. {summary or ''}")
    lower = text.lower()
    for phrase in keywords.get("exclude", []):
        if _has(lower, phrase):
            return FilterResult("dropped", f"exclude:{phrase}", [], [])
    pillars = [p for p, words in keywords.items() if p != "exclude" and any(_has(lower, w) for w in words)]
    tickers = sorted(
        {t for t in _TICKER.findall(text) if t in vn30}
        | {t for t, names in aliases.items() if t in vn30 and any(_has(lower, n) for n in names)}
    )
    if pillars or tickers:  # same rule for both streams; bias towards recall
        return FilterResult("kept", None, pillars, tickers)
    return FilterResult("dropped", "no_keyword" if stream == "A" else "no_ticker", [], [])


def is_duplicate_title(title: str, recent: list[tuple[str, str]]) -> str | None:
    """url_hash of the first `recent` (url_hash, title) at least 90% similar to `title`, else None."""
    a = _nfc(title).lower()
    for ref, other in recent:
        if difflib.SequenceMatcher(None, a, _nfc(other).lower()).ratio() >= DUPLICATE_RATIO:
            return ref
    return None


def get_vn30(conn) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT ticker FROM index_membership WHERE index_code = 'VN30' AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)")}


def refilter(conn, days: int) -> dict:
    """Re-run the current rules over stored RSS items (stream IS NOT NULL) of the last `days` days."""
    keywords, aliases, vn30 = load_keywords(), load_aliases(), get_vn30(conn)
    rows = conn.execute(
        "SELECT id, published_at, url_hash, title, summary, stream FROM news_items"
        " WHERE stream IS NOT NULL AND published_at >= now() - make_interval(days => %s) ORDER BY published_at",
        (days,),
    ).fetchall()
    kept: list[tuple] = []  # (published_at, url_hash, title)
    counts = {"rows": len(rows), "kept": 0, "dropped": 0}
    for id_, published_at, url_hash, title, summary, stream in rows:
        res = classify(title, summary, stream, vn30, aliases, keywords)
        status, reason = res.status, res.reason
        if status == "kept":
            recent = [(h, t) for p, h, t in kept if published_at - p <= DUPLICATE_WINDOW]
            dup = is_duplicate_title(title, recent)
            if dup:
                status, reason = "dropped", f"duplicate_title:{dup}"
            else:
                kept.append((published_at, url_hash, title))
        conn.execute(
            "UPDATE news_items SET filter_status = %s, filter_reason = %s, pillars = %s,"
            " tickers = (SELECT ARRAY(SELECT DISTINCT unnest(tickers || %s::text[])))"
            " WHERE id = %s AND published_at = %s",
            (status, reason, res.pillars, res.tickers, id_, published_at),
        )
        counts[status] += 1
    return counts


def main() -> None:
    from mcp_server.connection import get_rw_conn

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refilter", action="store_true", required=True)
    ap.add_argument("--days", type=int, required=True)
    args = ap.parse_args()
    with get_rw_conn() as conn:
        print(refilter(conn, args.days))


if __name__ == "__main__":
    main()
