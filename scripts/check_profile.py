"""Validate the per-user files and preview how the RulesEngine scores a few posts.

Usage:
    python scripts/check_profile.py                 # uses JOBTRACKER_HOME or resources/ + data/
    JOBTRACKER_HOME=examples/dana_example python scripts/check_profile.py

Checks that user_profile.yaml, scoring_rules.yaml and cv_structured.json load and that the
RulesEngine accepts the rules, and reports which company list sourcing will use. If the DB has posts, prints the top and bottom scores of a sample.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config_paths  # noqa: E402
from scoring.engines.rules import RulesEngine  # noqa: E402
from scoring.profile import cv_summary, load_profile  # noqa: E402
from sourcing.source_registry import load_confirmed  # noqa: E402


def main() -> int:
    print(f"Per-user files from: {config_paths.HOME or 'resources/ + data/ (default)'}")
    profile = load_profile()
    print(f"  profile ok     - {profile.get('name')}, {profile.get('seniority')}")
    print(f"  cv ok          - {cv_summary().splitlines()[1]}")
    engine = RulesEngine()
    print(f"  rules ok       - primary domain: {engine._rules['domains']['primary']['label']}")
    if config_paths.COMPANIES_PATH.exists():
        counts = {src: len(load_confirmed(src)) for src in ("greenhouse", "lever", "ashby", "comeet")}
        listed = ", ".join(f"{src} {n}" for src, n in counts.items() if n)
        print(f"  companies ok   - {sum(counts.values())} companies ({listed or 'none'})")
    else:
        print("  companies      - no companies.yaml: fetching the shipped demo registry")

    if not config_paths.DB_PATH.exists():
        print("  (no DB yet - ingest posts to preview scores)")
        return 0

    from ingestion.db.models import JobPost
    from ingestion.db.session import SessionLocal
    from scoring.service import _post_to_dict

    with SessionLocal() as session:
        posts = session.query(JobPost).limit(300).all()
        scored = sorted(
            ((engine.score(_post_to_dict(p)), p) for p in posts),
            key=lambda x: x[0].score, reverse=True,
        )
    if not scored:
        print("  (DB is empty)")
        return 0
    print(f"\nRules scores on {len(scored)} posts - top 5 / bottom 3:")
    for res, p in scored[:5] + scored[-3:]:
        print(f"  {res.score:5.0f}  {p.title[:60]:60}  {res.location_match[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
