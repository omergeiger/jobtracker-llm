"""
Greenhouse Job Board API adapter.

Fetches live job listings for a single company slug and maps them to RawJobPost
objects. No auth required; polling only.

API: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
"""

import logging
from datetime import datetime, timezone

import httpx

from ingestion.models import RawJobPost
from sourcing.job_source import JobSource

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def _parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _remote_type_from_metadata(metadata: list[dict]) -> str | None:
    """Extract remote type from Greenhouse metadata array (Location Type entry)."""
    for entry in metadata:
        if entry.get("name") == "Location Type":
            return entry.get("value")
    return None


def _map_job(job: dict, company_name: str) -> RawJobPost:
    remote_type_raw = _remote_type_from_metadata(job.get("metadata") or [])

    return RawJobPost(
        source_id="greenhouse",
        external_id=str(job["id"]),
        title=job["title"],
        company_name=job.get("company_name") or company_name,
        location_raw=(job.get("location") or {}).get("name", ""),
        apply_url=job["absolute_url"],
        raw_description=job.get("content", ""),
        posting_date=_parse_date(job.get("first_published")),
        metadata={
            "departments": job.get("departments", []),
            "offices": job.get("offices", []),
            "remote_type_raw": remote_type_raw,
            "updated_at": job.get("updated_at"),
        },
    )


class GreenhouseSource(JobSource):
    @property
    def source_id(self) -> str:
        return "greenhouse"

    def fetch(self, slug: str, company_name: str, location: str = "") -> list[RawJobPost]:
        logger.debug("Fetching Greenhouse: slug=%s, company=%s", slug, company_name)
        url = BASE_URL.format(slug=slug)
        resp = httpx.get(url, params={"content": "true"}, timeout=20)
        logger.debug("Greenhouse HTTP %d for slug=%s", resp.status_code, slug)

        if resp.status_code == 404:
            logger.debug("Greenhouse 404 - no board found for slug=%s", slug)
            return []
        resp.raise_for_status()

        jobs = resp.json().get("jobs", [])
        logger.info("Greenhouse: %d jobs from %s (%s)", len(jobs), company_name, slug)
        return [_map_job(job, company_name) for job in jobs]
