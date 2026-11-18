"""
Lever Job Postings API adapter.

API: GET https://api.lever.co/v0/postings/{slug}?mode=json
No auth required. Returns a top-level JSON array (or {"data": [...]}).
"Not found" can be HTTP 404 OR HTTP 200 with {"ok": false, ...}.
"""

import logging
from datetime import datetime, timezone

import httpx

from ingestion.models import RawJobPost
from sourcing.job_source import JobSource

logger = logging.getLogger(__name__)

BASE_URL = "https://api.lever.co/v0/postings/{slug}"


def _parse_unix_ms(value: int | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _map_job(job: dict, company_name: str) -> RawJobPost:
    cats = job.get("categories") or {}
    return RawJobPost(
        source_id="lever",
        external_id=job["id"],
        title=job["text"],
        company_name=company_name,
        location_raw=cats.get("location", ""),
        apply_url=job.get("hostedUrl", ""),
        raw_description=job.get("descriptionPlain") or job.get("description") or "",
        posting_date=_parse_unix_ms(job.get("createdAt")),
        metadata={
            "team": cats.get("team"),
            "department": cats.get("department"),
            "commitment": cats.get("commitment"),
            "workplace_type": job.get("workplaceType"),
        },
    )


class LeverSource(JobSource):
    @property
    def source_id(self) -> str:
        return "lever"

    def fetch(self, slug: str, company_name: str, location: str = "") -> list[RawJobPost]:
        logger.debug("Fetching Lever: slug=%s, company=%s", slug, company_name)
        url = BASE_URL.format(slug=slug)
        resp = httpx.get(url, params={"mode": "json"}, timeout=20)
        logger.debug("Lever HTTP %d for slug=%s", resp.status_code, slug)

        if resp.status_code == 404:
            logger.debug("Lever 404 - no board found for slug=%s", slug)
            return []
        resp.raise_for_status()

        data = resp.json()
        # Lever returns {"ok": false, "error": "..."} for unknown slugs via HTTP 200
        if isinstance(data, dict) and not data.get("ok", True):
            logger.debug("Lever unknown slug=%s: %s", slug, data.get("error"))
            return []

        jobs = data if isinstance(data, list) else data.get("data", [])
        logger.info("Lever: %d jobs from %s (%s)", len(jobs), company_name, slug)
        return [_map_job(job, company_name) for job in jobs]
