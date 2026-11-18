"""
Histogram of job count per score bucket, split by scoring engine.

No data is written back to the DB.

Usage:
    python scoring/ad_hoc/score_histogram.py
    python scoring/ad_hoc/score_histogram.py --bucket-size 10
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingestion.db import SessionLocal, JobPost, init_db

_BAR_WIDTH = 50


def _engine_name(raw: str | None) -> str:
    if not raw:
        return "unknown"
    return raw.split("/")[0]


def _bucket(score: float, size: int) -> int:
    return min(int(score) // size * size, 100 - size)


def main(bucket_size: int = 5) -> None:
    init_db()
    with SessionLocal() as s:
        rows = (
            s.query(JobPost.score, JobPost.score_breakdown)
            .filter(JobPost.score.isnot(None))
            .all()
        )

    counts: dict[str, Counter] = defaultdict(Counter)
    engines: set[str] = set()
    for score, breakdown in rows:
        engine = _engine_name((breakdown or {}).get("engine"))
        engines.add(engine)
        counts[engine][_bucket(score, bucket_size)] += 1

    engines = sorted(engines)
    buckets = list(range(0, 100, bucket_size))
    max_count = max((counts[e][b] for e in engines for b in buckets), default=0)

    totals = {e: sum(counts[e].values()) for e in engines}
    print(f"Bucket size: {bucket_size}")
    print("Totals: " + ", ".join(f"{e}={totals[e]}" for e in engines))
    print()

    header = f"{'Score':>9} | " + " | ".join(f"{e:>12}" for e in engines) + " | Bar (largest engine per row)"
    print(header)
    print("-" * len(header))

    for b in buckets:
        label = f"{b}-{b + bucket_size - 1}"
        row_counts = [counts[e][b] for e in engines]
        top_n = max(row_counts, default=0)
        bar = "#" * round(_BAR_WIDTH * top_n / max_count) if max_count else ""
        cells = " | ".join(f"{c:>12}" for c in row_counts)
        print(f"{label:>9} | {cells} | {bar}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket-size", type=int, default=5)
    args = parser.parse_args()
    main(bucket_size=args.bucket_size)
