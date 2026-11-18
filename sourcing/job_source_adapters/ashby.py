"""
Ashby Job Board API adapter.

API: GET https://api.ashbyhq.com/posting-api/job-board/{slug}
No auth required. Returns {"jobs": [...], "apiVersion": "..."}.
Note: 0 results is ambiguous — could be "not found" or "no open roles".
"""

import logging
from datetime import datetime, timezone

import httpx

from ingestion.models import RawJobPost
from sourcing.job_source import JobSource

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _map_job(job: dict, company_name: str) -> RawJobPost:
    return RawJobPost(
        source_id="ashby",
        external_id=job["id"],
        title=job["title"],
        company_name=company_name,
        location_raw=job.get("locationName") or job.get("location") or "",
        apply_url=job.get("jobUrl", ""),
        raw_description=job.get("descriptionPlain") or job.get("descriptionHtml") or "",
        posting_date=_parse_date(job.get("publishedAt")),
        metadata={
            "department": job.get("departmentName"),
            "workplace_type": job.get("workplaceType"),
            "is_remote": job.get("isRemote"),
            "employment_type": job.get("employmentType"),
        },
    )


class AshbySource(JobSource):
    @property
    def source_id(self) -> str:
        return "ashby"

    def fetch(self, slug: str, company_name: str, location: str = "") -> list[RawJobPost]:
        logger.debug("Fetching Ashby: slug=%s, company=%s", slug, company_name)
        url = BASE_URL.format(slug=slug)
        resp = httpx.get(url, timeout=20)
        logger.debug("Ashby HTTP %d for slug=%s", resp.status_code, slug)

        if resp.status_code == 404:
            logger.debug("Ashby 404 - no board found for slug=%s", slug)
            return []
        resp.raise_for_status()

        jobs = resp.json().get("jobs", [])
        logger.info("Ashby: %d jobs from %s (%s)", len(jobs), company_name, slug)
        return [_map_job(job, company_name) for job in jobs]
