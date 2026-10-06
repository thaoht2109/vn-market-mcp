"""Recall of the tier-1 news filter against hand labels (phase 1 gate: recall >= 95%).

  python -m evals.news_filter_recall --export 200 > evals/news_filter_labels.jsonl
      → then set "relevant": true/false on every line by hand ("relates to macro or a VN30 ticker?")
  python -m evals.news_filter_recall --score evals/news_filter_labels.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys

from pipeline.news_filter import classify, get_vn30, load_aliases, load_keywords

GATE_RECALL = 0.95


def score(rows: list[dict], vn30: set[str], aliases, keywords) -> dict:
    labelled = [r for r in rows if r.get("relevant") is not None]
    tp = fp = fn = 0
    missed = []
    for r in labelled:
        kept = classify(r["title"], r.get("summary"), r.get("stream", "A"), vn30, aliases, keywords).status == "kept"
        if r["relevant"] and kept:
            tp += 1
        elif r["relevant"]:
            fn += 1
            missed.append(r)
        elif kept:
            fp += 1
    if tp + fn == 0:
        raise ValueError("no row labelled relevant=true: recall is undefined")
    return {"labelled": len(labelled), "recall": tp / (tp + fn),
            "precision": tp / (tp + fp) if tp + fp else 1.0, "missed": missed}


def main() -> int:
    from mcp_server.connection import get_rw_conn

    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", type=int, metavar="N")
    g.add_argument("--score", metavar="FILE")
    args = ap.parse_args()
    with get_rw_conn() as conn:
        if args.export:
            for title, summary, stream in conn.execute(
                    "SELECT title, summary, stream FROM news_items WHERE stream IS NOT NULL"
                    " ORDER BY published_at DESC LIMIT %s", (args.export,)):
                print(json.dumps({"title": title, "summary": summary, "stream": stream, "relevant": None},
                                 ensure_ascii=False))
            return 0
        vn30 = get_vn30(conn)
    rows = [json.loads(line) for line in open(args.score, encoding="utf-8") if line.strip()]
    r = score(rows, vn30, load_aliases(), load_keywords())
    print(f"labelled={r['labelled']} recall={r['recall']:.1%} precision={r['precision']:.1%} (gate: recall >= {GATE_RECALL:.0%})")
    for m in r["missed"]:
        print("MISSED:", m["title"])
    return 0 if r["recall"] >= GATE_RECALL else 1


if __name__ == "__main__":
    sys.exit(main())
