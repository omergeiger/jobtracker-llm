#!/usr/bin/env python3
"""
Check whether still-open job posts are actually closed at their source, and mark
confirmed closures with status='closed'.

Only posts with status in ('new', 'reviewed') are checked -- applied/rejected/
archived/already-closed posts are left alone.

Detection, by source:
  - greenhouse / lever / ashby: re-fetch the company's current listing via the
    existing sourcing adapter. A DB post whose external_id is no longer in that
    fresh listing has been closed or pulled by the recruiter. No false-positive
    risk -- the source's own API is the ground truth.
  - linkedin: fetch the post's own apply_url and look for LinkedIn's server-rendered
    "No longer accepting applications" banner (class="closed-job__flavor--closed").
    In testing this only caught ~7 of 9 known-closed LinkedIn posts (LinkedIn doesn't
    always render the banner to anonymous requests) -- posts that don't show it are
    left untouched rather than guessed at, consistent with never silently dropping
    a post that might still be open.
  - comeet: skipped -- no confirmed registry slugs currently (migrated to Ashby).

Usage:
    python scripts/check_closed_postings.py
    python scripts/check_closed_postings.py --dry-run
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import httpx

from ingestion.db import SessionLocal, init_db, JobPost, Source
from sourcing.job_source_adapters import AshbySource, GreenhouseSource, LeverSource
from sourcing.source_registry import load_confirmed

CHECKABLE_STATUSES = ("new", "reviewed")

LISTING_ADAPTERS = {
    "ashby": AshbySource,
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
}

LINKEDIN_CLOSED_MARKER = "closed-job__flavor--closed"


def _normalize(name: str) -> str:
    """Collapse to lowercase alphanumerics so DB company names (which come from
    each source's own API response, not the registry) can be matched against
    registry slugs despite formatting drift, e.g. "Gong.io" vs slug "gongio"."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _find_listing_closures(source_name: str, posts: list[JobPost]) -> list[JobPost]:
    slug_by_normalized = {_normalize(e["slug"]): e["slug"] for e in load_confirmed(source_name)}

    by_company: dict[str, list[JobPost]] = {}
    for p in posts:
        by_company.setdefault(p.company.name, []).append(p)

    adapter = LISTING_ADAPTERS[source_name]()
    closed: list[JobPost] = []
    for company_name, company_posts in by_company.items():
        slug = slug_by_normalized.get(_normalize(company_name))
        if not slug:
            print(f"  [{source_name}] skipping {company_name}: not in confirmed registry")
            continue
        try:
            live_posts = adapter.fetch(slug, company_name)
        except Exception as exc:
            print(f"  [{source_name}] fetch failed for {company_name} ({slug}): {exc}")
            continue
        live_ids = {p.external_id for p in live_posts}
        closed.extend(p for p in company_posts if p.external_id not in live_ids)
    return closed


def _find_linkedin_closures(posts: list[JobPost]) -> list[JobPost]:
    closed: list[JobPost] = []
    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for p in posts:
            if not p.apply_url:
                continue
            try:
                resp = client.get(p.apply_url)
            except Exception as exc:
                print(f"  [linkedin] fetch failed for {p.title!r}: {exc}")
                continue
            if resp.status_code == 200 and LINKEDIN_CLOSED_MARKER in resp.text:
                closed.append(p)
    return closed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report findings without writing status changes")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as session:
        posts = (
            session.query(JobPost)
            .join(Source)
            .filter(JobPost.status.in_(CHECKABLE_STATUSES))
            .all()
        )

        by_source: dict[str, list[JobPost]] = {}
        for p in posts:
            by_source.setdefault(p.source.name, []).append(p)

        total_closed = 0
        for source_name, source_posts in sorted(by_source.items()):
            if source_name in LISTING_ADAPTERS:
                closed = _find_listing_closures(source_name, source_posts)
            elif source_name == "linkedin":
                closed = _find_linkedin_closures(source_posts)
            else:
                print(f"  [{source_name}] no closure check available -- skipping {len(source_posts)} post(s)")
                continue

            for p in closed:
                print(f"  CLOSED: [{source_name}] {p.company.name} - {p.title}")
                if not args.dry_run:
                    p.status = "closed"
                    p.global_rerank_position = None
                    p.rerank_rationale = None
            total_closed += len(closed)

        if not args.dry_run:
            session.commit()

        suffix = " (dry run, not saved)" if args.dry_run else ""
        print(f"\nChecked {len(posts)} open post(s) across {len(by_source)} source(s); "
              f"{total_closed} marked closed{suffix}.")


if __name__ == "__main__":
    main()
