"""
Comeet Job Board API adapter.

API: GET https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}
No auth required. Returns a top-level JSON array of position objects.

Registry slugs encode both values as "{uid}:{token}" (e.g. "41.00B:14B52C52C67790D3E1296BA37C20").
Note: descriptions are not available from this API — raw_description is always empty.
See sourcing/CLAUDE.md "Comeet description options" for future approaches.
"""

import logging
from datetime import datetime, timezone

import httpx

from ingestion.models import RawJobPost
from sourcing.job_source import JobSource

logger = logging.getLogger(__name__)

BASE_URL = "https://www.comeet.co/careers-api/2.0/company/{uid}/positions"


def _parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _map_job(job: dict, company_name: str) -> RawJobPost:
    location = job.get("location") or {}
    apply_url = job.get("url_active_page") or job.get("url_comeet_hosted_page") or ""

    return RawJobPost(
        source_id="comeet",
        external_id=job["uid"],
        title=job["name"],
        company_name=job.get("company_name") or company_name,
        location_raw=location.get("name") or "",
        apply_url=apply_url,
        raw_description="",
        posting_date=_parse_date(job.get("time_updated")),
        metadata={
            "department": job.get("department"),
            "workplace_type": job.get("workplace_type"),
            "employment_type": job.get("employment_type"),
            "experience_level": job.get("experience_level") or None,
            "is_remote": location.get("is_remote"),
        },
    )


class ComeetSource(JobSource):
    @property
    def source_id(self) -> str:
        return "comeet"

    def fetch(self, slug: str, company_name: str, location: str = "") -> list[RawJobPost]:
        if ":" not in slug:
            raise ValueError(f"Comeet slug must be 'uid:token', got: {slug!r}")

        uid, token = slug.split(":", 1)
        logger.debug("Fetching Comeet: uid=%s, company=%s", uid, company_name)
        url = BASE_URL.format(uid=uid)
        resp = httpx.get(url, params={"token": token}, timeout=20)
        logger.debug("Comeet HTTP %d for uid=%s", resp.status_code, uid)

        if resp.status_code == 404:
            logger.debug("Comeet 404 - no board found for uid=%s", uid)
            return []
        resp.raise_for_status()

        jobs = resp.json()
        if not isinstance(jobs, list):
            logger.warning("Comeet unexpected response format for uid=%s: %s", uid, type(jobs).__name__)
            return []

        public = [job for job in jobs if not job.get("is_internal")]
        logger.info("Comeet: %d jobs from %s (uid=%s, %d internal filtered)", len(public), company_name, uid, len(jobs) - len(public))
        return [_map_job(job, company_name) for job in public]
