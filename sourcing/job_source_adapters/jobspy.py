"""
JobSpy LinkedIn adapter.

Scrapes LinkedIn job postings via python-jobspy. Unlike the ATS adapters,
this is search-based: `slug` is a search query string (e.g. "machine learning
engineer") and `location` is taken from the registry entry.

Results are deduplicated by apply_url downstream; duplicates across search
queries for the same role are handled by the SHA-256 dedup in IngestService.

Unauthenticated only: LinkedIn's public search results carry no description,
so raw_description is empty for these posts.

Disabled by default (`enabled: false` in jobspy.yaml) and not part of the
default pipeline. Automated access to LinkedIn may violate LinkedIn's User
Agreement (https://www.linkedin.com/legal/user-agreement). Check its terms
before enabling this source; use at your own risk.
"""

import logging
from datetime import date, datetime, timezone

from ingestion.models import RawJobPost
from sourcing.job_source import JobSource

logger = logging.getLogger(__name__)

DEFAULT_RESULTS = 100
DEFAULT_HOURS_OLD = 168  # 7 days


def _to_datetime(value) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _safe_str(value) -> str:
    if value is None:
        return ""
    import math
    try:
        if isinstance(value, float) and math.isnan(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


class JobSpySource(JobSource):
    """Search-based LinkedIn scraper. Each registry entry is one search query."""

    def __init__(self, results_wanted: int = DEFAULT_RESULTS, hours_old: int = DEFAULT_HOURS_OLD):
        self._results_wanted = results_wanted
        self._hours_old = hours_old

    @property
    def source_id(self) -> str:
        return "linkedin"

    def fetch(self, slug: str, company_name: str, location: str = "") -> list[RawJobPost]:
        from jobspy import scrape_jobs

        # No default location: an entry without one searches everywhere (set it in jobspy.yaml).
        effective_location = location or None
        logger.debug(
            "LinkedIn scrape: query=%r, location=%s, results_wanted=%d, hours_old=%d",
            slug, effective_location, self._results_wanted, self._hours_old,
        )

        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=slug,
            location=effective_location,
            results_wanted=self._results_wanted,
            hours_old=self._hours_old,
            verbose=0,
        )

        if df is None or df.empty:
            logger.info("LinkedIn: 0 results for query=%r, location=%s", slug, effective_location)
            return []

        posts = []
        skipped = 0
        for _, row in df.iterrows():
            apply_url = _safe_str(row.get("job_url")) or _safe_str(row.get("job_url_direct"))
            if not apply_url:
                skipped += 1
                continue

            posts.append(RawJobPost(
                source_id="linkedin",
                external_id=_safe_str(row.get("id")) or apply_url,
                title=_safe_str(row.get("title")),
                company_name=_safe_str(row.get("company")) or company_name,
                location_raw=_safe_str(row.get("location")),
                apply_url=apply_url,
                raw_description=_safe_str(row.get("description")),
                posting_date=_to_datetime(row.get("date_posted")),
                metadata={
                    "job_type": _safe_str(row.get("job_type")),
                    "is_remote": row.get("is_remote"),
                    "search_query": slug,
                    "search_location": location,
                },
            ))

        if skipped:
            logger.warning("LinkedIn: %d rows skipped (no apply_url) for query=%r", skipped, slug)
        logger.info("LinkedIn: %d jobs for query=%r, location=%s", len(posts), slug, effective_location)
        return posts
