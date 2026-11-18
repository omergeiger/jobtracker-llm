"""
Benchmark OllamaEngine (or RulesEngine) against a sample of posts from the DB.

No data is written back — the session is never committed.

When --output is given, each scored post is appended to a JSONL file immediately
after it completes, so progress survives an early exit or crash.

Usage:
    python scoring/ad_hoc/benchmark.py
    python scoring/ad_hoc/benchmark.py --engine rules
    python scoring/ad_hoc/benchmark.py --limit 20
    python scoring/ad_hoc/benchmark.py --output scoring/ad_hoc/runs/results.jsonl
"""

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path
from typing import IO

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingestion.db import SessionLocal, JobPost, init_db
from scoring.engines.base import ScoringEngine
from scoring.service import _build_engine, _post_to_dict


def _sample_posts(limit: int) -> list[JobPost]:
    with SessionLocal() as session:
        return (
            session.query(JobPost)
            .order_by(JobPost.id)
            .limit(limit)
            .all()
        )


def _percentile(sorted_data: list[float], pct: float) -> float:
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (k - lo) * (sorted_data[hi] - sorted_data[lo])


def _print_summary(engine: ScoringEngine, durations: list[float], errors: list[dict]) -> dict:
    if not durations:
        print("No successful scores — cannot compute statistics.")
        return {}

    sorted_d = sorted(durations)
    stats = {
        "n": len(durations),
        "errors": len(errors),
        "total_s": round(sum(durations), 2),
        "min_s": round(sorted_d[0], 3),
        "max_s": round(sorted_d[-1], 3),
        "mean_s": round(statistics.mean(durations), 3),
        "median_s": round(statistics.median(durations), 3),
        "p95_s": round(_percentile(sorted_d, 95), 3),
        "stdev_s": round(statistics.stdev(durations), 3) if len(durations) > 1 else 0.0,
    }

    print("═" * 50)
    print(f"  Engine   : {engine.name}")
    print(f"  Scored   : {stats['n']}   Errors: {stats['errors']}")
    print(f"  Total    : {stats['total_s']:.1f}s")
    print(f"  Min/Max  : {stats['min_s']:.2f}s / {stats['max_s']:.2f}s")
    print(f"  Mean     : {stats['mean_s']:.2f}s   Median: {stats['median_s']:.2f}s")
    print(f"  p95      : {stats['p95_s']:.2f}s   StdDev: {stats['stdev_s']:.2f}s")
    print("═" * 50)

    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  [{e['index']}] {e['title']}: {e['error']}")

    return stats


def run_benchmark(
    engine: ScoringEngine,
    posts: list[JobPost],
    out: IO | None = None,
) -> dict:
    durations: list[float] = []
    errors: list[dict] = []

    print(f"{'#':<5} {'Title':<45} {'Company':<20} {'Score':>6} {'Time':>8}")
    print("-" * 90)

    for i, post in enumerate(posts, 1):
        post_dict = _post_to_dict(post)
        t0 = time.perf_counter()
        try:
            result = engine.score(post_dict)
            elapsed = time.perf_counter() - t0
            durations.append(elapsed)

            title = (post.title or "")[:44]
            company = (post_dict.get("company_name") or post.company_name or "")[:19]
            print(f"{i:<5} {title:<45} {company:<20} {result.score:>6.1f} {elapsed:>7.2f}s",
                  flush=True)

            if out:
                out.write(json.dumps({
                    "index": i,
                    "title": post.title,
                    "company": post_dict.get("company_name") or post.company_name,
                    "score": result.score,
                    "engine": result.engine,
                    "duration_s": round(elapsed, 3),
                    "dealbreaker": result.dealbreaker,
                    "summary": result.summary,
                }) + "\n")
                out.flush()

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            msg = str(exc)
            print(f"{i:<5} {'ERROR':<45} {'':<20} {'—':>6} {elapsed:>7.2f}s  ← {msg[:40]}",
                  flush=True)
            errors.append({"index": i, "title": post.title, "error": msg})

            if out:
                out.write(json.dumps({
                    "index": i,
                    "title": post.title,
                    "error": msg,
                }) + "\n")
                out.flush()

    print()
    stats = _print_summary(engine, durations, errors)
    return {"engine": engine.name, "stats": stats, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark scoring engine against DB posts")
    parser.add_argument("--engine", choices=["auto", "ollama", "rules"], default="auto")
    parser.add_argument("--model", default=None, metavar="NAME",
                        help="Ollama model to use, e.g. llama3.1:70b (only applies to "
                             "--engine ollama/auto; default: qwen2.5:7b)")
    parser.add_argument("--limit", type=int, default=100, metavar="N",
                        help="Number of posts to sample (default: 100)")
    parser.add_argument("--output", metavar="FILE",
                        help="Append each result as a JSON line to FILE immediately after scoring")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    init_db()
    posts = _sample_posts(args.limit)
    if not posts:
        print("No posts found in DB. Run `python -m sourcing.run_sourcing --ingest`, "
              "or set JOBTRACKER_HOME=examples/dana_example.")
        return

    engine = _build_engine(args.engine, model=args.model)
    print(f"\nEngine : {engine.name}")
    print(f"Sample : {len(posts)} posts\n")

    if args.output:
        out_path = Path(args.output)
        print(f"Output : {out_path} (streaming JSONL)\n")
        with out_path.open("w") as f:
            run_benchmark(engine, posts, out=f)
    else:
        run_benchmark(engine, posts, out=None)


if __name__ == "__main__":
    main()
