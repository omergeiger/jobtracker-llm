"""
Compare Ollama vs RulesEngine scores on all Ollama-scored posts in the DB.

No data is written back to the DB.

Output (versioned by timestamp, never overwritten):
  - scoring/ad_hoc/runs/comparison_<ts>.jsonl   — one record per post
  - scoring/ad_hoc/runs/comparison_<ts>.txt     — the full printed summary

Usage:
    python scoring/ad_hoc/compare_engines.py
"""

import io
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import func

from ingestion.db import SessionLocal, JobPost, init_db
from scoring.engines.rules import RulesEngine
from scoring.service import _post_to_dict

_RUNS_DIR = Path(__file__).parent / "runs"


class _Tee:
    """Writes to stdout and a StringIO buffer simultaneously."""
    def __init__(self):
        self._buf = io.StringIO()

    def write(self, msg: str) -> None:
        sys.stdout.write(msg)
        self._buf.write(msg)

    def flush(self) -> None:
        sys.stdout.flush()

    def getvalue(self) -> str:
        return self._buf.getvalue()


def _stats(vals: list[float]) -> dict:
    s = sorted(vals)

    def pct(p: float) -> float:
        k = (len(s) - 1) * p / 100
        lo, hi = int(k), min(int(k) + 1, len(s) - 1)
        return s[lo] + (k - lo) * (s[hi] - s[lo])

    return dict(
        mean=round(statistics.mean(vals), 1),
        median=round(statistics.median(vals), 1),
        stdev=round(statistics.stdev(vals), 1),
        p10=round(pct(10), 1),
        p25=round(pct(25), 1),
        p75=round(pct(75), 1),
        p90=round(pct(90), 1),
    )


def _bucket(score: float) -> str:
    if score >= 70:
        return "70-100"
    if score >= 55:
        return "55-69"
    if score >= 40:
        return "40-54"
    return "0-39"


def main(train_n: int = 2065) -> None:
    _RUNS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = _RUNS_DIR / f"comparison_{ts}.jsonl"
    summary_path = _RUNS_DIR / f"comparison_{ts}.txt"

    init_db()
    engine = RulesEngine()

    with SessionLocal() as s:
        posts = (
            s.query(JobPost)
            .filter(func.json_extract(JobPost.score_breakdown, "$.engine").like("ollama%"))
            .order_by(JobPost.id)
            .all()
        )

        rows = []
        for post in posts:
            post_dict = _post_to_dict(post)
            rules_result = engine.score(post_dict)
            bd = post.score_breakdown or {}
            rows.append({
                "id": post.id,
                "title": post.title,
                "company": post_dict.get("company_name") or post.company_name,
                "ollama_score": post.score,
                "rules_score": rules_result.score,
                "delta": round(post.score - rules_result.score, 1),
                "ollama_engine": bd.get("engine", ""),
                "ollama_summary": bd.get("summary", ""),
                "rules_dealbreaker": rules_result.dealbreaker,
                "ollama_dealbreaker": bd.get("dealbreaker", False),
            })

    with open(jsonl_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    tee = _Tee()
    tee.write(f"Saved {len(rows)} records → {jsonl_path}\n\n")

    train = rows[:train_n]
    test  = rows[train_n:]

    _print_analysis(train, label=f"TRAIN (first {len(train)})", out=tee)
    if test:
        tee.write("\n" + "═" * 70 + "\n\n")
        _print_analysis(test, label=f"TEST  (remaining {len(test)})", out=tee)

    summary_path.write_text(tee.getvalue())
    sys.stdout.write(f"\nSummary saved → {summary_path}\n")


def _print_analysis(rows: list[dict], label: str, out: _Tee) -> None:
    n = len(rows)
    if n == 0:
        out.write(f"=== {label} — no records ===\n")
        return

    ollama_scores = [r["ollama_score"] for r in rows]
    rules_scores  = [r["rules_score"]  for r in rows]
    deltas        = [r["delta"]        for r in rows]

    os_ = _stats(ollama_scores)
    rs_ = _stats(rules_scores)
    ds_ = _stats(deltas)

    agree_10  = sum(1 for d in deltas if abs(d) <= 10)
    agree_20  = sum(1 for d in deltas if abs(d) <= 20)
    rules_hi  = sum(1 for d in deltas if d < -20)
    ollama_hi = sum(1 for d in deltas if d >  20)

    top_under = sorted(rows, key=lambda r: r["delta"], reverse=True)[:20]
    top_over  = sorted(rows, key=lambda r: r["delta"])[:20]

    ob = Counter(_bucket(r["ollama_score"]) for r in rows)
    rb = Counter(_bucket(r["rules_score"])  for r in rows)

    out.write(f"=== {label} ===\n\n")
    out.write("Score statistics:\n")
    out.write(f"  {'Metric':<12} {'Ollama':>8} {'Rules':>8} {'Delta (O-R)':>12}\n")
    out.write(f"  {'':12} {'------':>8} {'-----':>8} {'-----------':>12}\n")
    for k in ["mean", "median", "stdev", "p10", "p25", "p75", "p90"]:
        out.write(f"  {k:<12} {os_[k]:>8} {rs_[k]:>8} {ds_[k]:>12}\n")

    out.write(f"\nAgreement (|delta| ≤ 10): {agree_10}/{n} = {100*agree_10//n}%\n")
    out.write(f"Agreement (|delta| ≤ 20): {agree_20}/{n} = {100*agree_20//n}%\n")
    out.write(f"Rules >> Ollama (d < -20): {rules_hi}  ({100*rules_hi//n}%)\n")
    out.write(f"Ollama >> Rules (d >  20): {ollama_hi} ({100*ollama_hi//n}%)\n")

    out.write("\nScore bucket distribution:\n")
    out.write(f"  {'Bucket':<10} {'Ollama':>8} {'Rules':>8}\n")
    for b in ["70-100", "55-69", "40-54", "0-39"]:
        out.write(f"  {b:<10} {ob[b]:>8} {rb[b]:>8}\n")

    out.write(f"\nTop 20 — Ollama >> Rules (rules UNDERSCORING):\n")
    out.write(f"  {'Delta':>6}  {'Ollama':>6}  {'Rules':>6}  {'Title':<50}  Company\n")
    for r in top_under:
        out.write(f"  {r['delta']:>+6.0f}  {r['ollama_score']:>6.0f}  {r['rules_score']:>6.0f}  {r['title'][:50]:<50}  {r['company']}\n")

    out.write(f"\nTop 20 — Rules >> Ollama (rules OVERSCORING):\n")
    out.write(f"  {'Delta':>6}  {'Ollama':>6}  {'Rules':>6}  {'Title':<50}  Company\n")
    for r in top_over:
        out.write(f"  {r['delta']:>+6.0f}  {r['ollama_score']:>6.0f}  {r['rules_score']:>6.0f}  {r['title'][:50]:<50}  {r['company']}\n")


if __name__ == "__main__":
    main()
