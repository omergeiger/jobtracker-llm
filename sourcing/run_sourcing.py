"""
Run the sourcing pipeline for all (or selected) ATS sources.

By default runs all sources found in sourcing/registry/. Use --source to
limit to specific sources, or --slug for an ad-hoc single-slug test without
touching the registry.

Usage:
    python -m sourcing.run_sourcing
    python -m sourcing.run_sourcing --source greenhouse lever
    python -m sourcing.run_sourcing --source ashby --slug openai
    python -m sourcing.run_sourcing --ingest
    python -m sourcing.run_sourcing --dry-run
    python -m sourcing.run_sourcing --source jobspy --from-files
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv
load_dotenv()
from pathlib import Path

from logging_config import setup_logging
from ingestion.models import RawJobPost
from sourcing.job_source import JobSource
from sourcing.job_source_adapters import AshbySource, ComeetSource, GreenhouseSource, JobSpySource, LeverSource
from sourcing.source_registry import load_confirmed, list_sources

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent

SOURCES: dict[str, type[JobSource]] = {
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "ashby": AshbySource,
    "jobspy": JobSpySource,
    "comeet": ComeetSource,
}


@dataclass
class SlugResult:
    slug: str
    company_name: str
    job_count: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class SourceRunResult:
    source_id: str
    slug_results: list[SlugResult] = field(default_factory=list)
    ingest_summary: str | None = None

    @property
    def total_jobs(self) -> int:
        return sum(r.job_count for r in self.slug_results)

    @property
    def errors(self) -> list[SlugResult]:
        return [r for r in self.slug_results if not r.ok]


def _load_from_file(output_dir: Path, slug: str) -> list[RawJobPost] | None:
    """Read previously fetched posts from disk. Returns None if file is missing."""
    path = output_dir / f"{slug}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return [RawJobPost.model_validate(d) for d in data]


def _run_source(
    source_id: str,
    companies: list[dict],
    output_dir: Path,
    ingest: bool,
    dry_run: bool,
    from_files: bool = False,
) -> SourceRunResult:
    result = SourceRunResult(source_id=source_id)
    all_posts = []

    if not from_files and not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    source = None if from_files else SOURCES[source_id]()

    for entry in companies:
        slug, name = entry["slug"], entry["name"]
        try:
            if from_files:
                posts = _load_from_file(output_dir, slug)
                if posts is None:
                    slug_result = SlugResult(slug=slug, company_name=name, error="file not found")
                    logger.error("File not found for slug=%s (expected %s)", slug, output_dir / f"{slug}.json")
                    result.slug_results.append(slug_result)
                    continue
            else:
                posts = source.fetch(slug, name, location=entry.get("location", ""))
                if posts and not dry_run:
                    out_path = output_dir / f"{slug}.json"
                    out_path.write_text(
                        json.dumps([p.model_dump(mode="json") for p in posts], indent=2)
                    )

            slug_result = SlugResult(slug=slug, company_name=name, job_count=len(posts))
            all_posts.extend(posts)
        except Exception as exc:
            slug_result = SlugResult(slug=slug, company_name=name, error=str(exc))
            logger.error("Fetch failed: source=%s slug=%s: %s", source_id, slug, exc)

        status = f"{slug_result.job_count} jobs" if slug_result.ok else f"ERROR: {slug_result.error}"
        print(f"  {slug:<20} {status}")
        result.slug_results.append(slug_result)

    if (ingest or from_files) and all_posts and not dry_run:
        # Deduplicate within the batch before IngestService sees it — multiple
        # search queries can return the same job, which would cause a batch-level
        # UNIQUE constraint violation before IngestService's own dedup check runs.
        seen: set[str] = set()
        unique_posts = []
        for p in all_posts:
            if p.apply_url not in seen:
                seen.add(p.apply_url)
                unique_posts.append(p)
        dupes = len(all_posts) - len(unique_posts)
        if dupes:
            logger.info("Within-batch dedup: removed %d duplicates from %s (%d -> %d)", dupes, source_id, len(all_posts), len(unique_posts))
            print(f"  Deduped    : {dupes} within-batch duplicates removed")
        from ingestion.pipeline import IngestService
        ingest_result = IngestService().ingest(unique_posts)
        result.ingest_summary = str(ingest_result)

    return result


def _print_source_summary(result: SourceRunResult) -> None:
    empty = [r for r in result.slug_results if r.ok and r.job_count == 0]
    print(f"  Total jobs : {result.total_jobs}")
    if empty:
        print(f"  Empty/404  : {len(empty)} ({', '.join(r.slug for r in empty)})")
    if result.errors:
        print(f"  Errors     : {len(result.errors)} ({', '.join(r.slug for r in result.errors)})")
    if result.ingest_summary:
        print(f"  Ingest     : {result.ingest_summary}")


def main() -> None:
    available = list_sources()

    parser = argparse.ArgumentParser(description="Run sourcing pipeline for all or selected sources.")
    parser.add_argument(
        "--source",
        nargs="+",
        choices=sorted(SOURCES),
        metavar="SOURCE",
        help=f"Sources to run (default: all configured — {', '.join(available)})",
    )
    parser.add_argument(
        "--slug",
        help="Ad-hoc single slug (requires --source with exactly one value; bypasses registry)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory override (default: sourcing/output/{source})",
    )
    parser.add_argument("--ingest", action="store_true", help="Ingest into DB after fetch")
    parser.add_argument("--dry-run", action="store_true", help="Fetch only — no writes")
    parser.add_argument(
        "--from-files",
        action="store_true",
        help="Ingest from existing JSON output files instead of re-fetching (implies --ingest)",
    )
    parser.add_argument(
        "--score",
        action="store_true",
        help="Score new posts with rules engine immediately after ingest; use scoring.service --upgrade-rules for Ollama backfill",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show DEBUG-level log output on stderr (file always gets DEBUG)",
    )
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if args.from_files and args.slug:
        parser.error("--from-files cannot be used with --slug")
    if args.from_files and args.dry_run:
        parser.error("--from-files cannot be used with --dry-run")

    if args.slug:
        if not args.source or len(args.source) != 1:
            parser.error("--slug requires exactly one --source")
        sources_to_run = args.source
    else:
        sources_to_run = args.source or available

    total_jobs = 0
    total_errors = 0

    for source_id in sources_to_run:
        output_dir = args.output or (ROOT / "sourcing" / "output" / source_id)

        if args.slug:
            companies = [{"slug": args.slug, "name": args.slug}]
            print(f"\n[{source_id}] Probing slug '{args.slug}'...")
        else:
            companies = load_confirmed(source_id)
            verb = "Loading from files" if args.from_files else "Fetching"
            print(f"\n[{source_id}] {verb} {len(companies)} confirmed slug(s)...")

        result = _run_source(source_id, companies, output_dir, args.ingest, args.dry_run, args.from_files)
        _print_source_summary(result)
        total_jobs += result.total_jobs
        total_errors += len(result.errors)

    if len(sources_to_run) > 1:
        print(f"\n{'═' * 40}")
        print(f"Sources run : {len(sources_to_run)}")
        print(f"Total jobs  : {total_jobs}")
        if total_errors:
            print(f"Total errors: {total_errors}")

    if args.score and not args.dry_run:
        if not args.ingest and not args.from_files:
            print("\nNote: --score has no effect without --ingest or --from-files")
        else:
            from scoring.service import ScoringService
            print(f"\n{'═' * 40}")
            print("Scoring new posts (rules engine)...")
            scoring_result = ScoringService(engine="rules").score_posts()
            print(f"  {scoring_result}")
            print("  Ollama backfill: python -m scoring.service --upgrade-rules")


if __name__ == "__main__":
    main()
