import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

from ingestion.db import SessionLocal, init_db, JobPost, Company, Source
from ingestion.extraction import extract_fields
from ingestion.models import RawJobPost

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    ingested: int = 0
    enriched: int = 0
    skipped_duplicates: int = 0
    skipped_by_source: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        source_breakdown = ""
        if self.skipped_by_source:
            parts = ", ".join(f"{src}={n}" for src, n in sorted(self.skipped_by_source.items()))
            source_breakdown = f" ({parts})"
        return (
            f"Ingested: {self.ingested} | "
            f"Enriched: {self.enriched} | "
            f"Duplicates skipped: {self.skipped_duplicates}{source_breakdown} | "
            f"Errors: {len(self.errors)}"
        )


def _normalize_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _dedup_hash(raw: RawJobPost) -> str:
    if raw.source_id == "linkedin":
        # LinkedIn assigns a new job ID on every repost; URL is unstable.
        # Use location_raw as the stable semantic anchor instead.
        key = (
            f"{raw.company_name.lower().strip()}"
            f":{raw.title.lower().strip()}"
            f":{raw.location_raw.lower().strip()}"
        )
    else:
        key = (
            f"{raw.company_name.lower().strip()}"
            f":{raw.title.lower().strip()}"
            f":{_normalize_url(raw.apply_url)}"
        )
    return hashlib.sha256(key.encode()).hexdigest()


def _get_or_create_source(session, source_id: str) -> Source:
    source = session.query(Source).filter_by(name=source_id).first()
    if not source:
        source = Source(
            name=source_id,
            type="api",
            last_fetched_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.flush()
    return source


def _get_or_create_company(session, name: str) -> Company:
    company = session.query(Company).filter_by(name=name).first()
    if not company:
        company = Company(name=name)
        session.add(company)
        session.flush()
    return company


class IngestService:
    """
    Accepts RawJobPost objects from any source and persists them to SQLite.
    Responsibilities: deduplication, field extraction, DB writes.
    Does not know or care where the raw posts came from.
    """

    def __init__(self) -> None:
        init_db()

    def ingest(self, raw_posts: list[RawJobPost]) -> IngestResult:
        logger.info("Starting ingest: %d posts", len(raw_posts))
        result = IngestResult()
        new_posts: list[JobPost] = []
        enriched_posts: list[JobPost] = []

        with SessionLocal() as session:
            for raw in raw_posts:
                try:
                    # Isolate each post in its own savepoint so a bad post (e.g. an
                    # in-batch dedup_hash collision surfacing on a later flush) only
                    # rolls back its own work, not every post already queued behind it.
                    with session.begin_nested():
                        dedup_hash = _dedup_hash(raw)
                        existing = session.query(JobPost).filter_by(dedup_hash=dedup_hash).first()

                        if existing:
                            if not existing.raw_description and raw.raw_description:
                                logger.debug("Enriching: %s / %s", raw.company_name, raw.title)
                                existing.raw_description = raw.raw_description
                                existing.extracted_fields = extract_fields(raw)
                                enriched_posts.append(existing)
                                result.enriched += 1
                            else:
                                logger.debug("Duplicate skipped: %s / %s", raw.company_name, raw.title)
                                result.skipped_duplicates += 1
                                result.skipped_by_source[raw.source_id] = (
                                    result.skipped_by_source.get(raw.source_id, 0) + 1
                                )
                            continue

                        logger.debug("New post: %s / %s", raw.company_name, raw.title)
                        source = _get_or_create_source(session, raw.source_id)
                        company = _get_or_create_company(session, raw.company_name)

                        job_post = JobPost(
                            source_id=source.id,
                            company_id=company.id,
                            external_id=raw.external_id,
                            title=raw.title,
                            raw_description=raw.raw_description,
                            apply_url=raw.apply_url,
                            extracted_fields=extract_fields(raw),
                            posting_date=raw.posting_date,
                            dedup_hash=dedup_hash,
                        )
                        session.add(job_post)
                        new_posts.append(job_post)
                        result.ingested += 1

                except Exception as exc:
                    msg = f"{raw.company_name} / {raw.title}: {exc}"
                    result.errors.append(msg)
                    logger.error("Ingest error: %s", msg)

            source_names = {r.source_id for r in raw_posts}
            for name in source_names:
                src = session.query(Source).filter_by(name=name).first()
                if src:
                    src.last_fetched_at = datetime.now(timezone.utc)

            session.flush()  # assign IDs for new posts before commit
            new_post_ids = [p.id for p in new_posts]
            enriched_post_ids = [p.id for p in enriched_posts]
            session.commit()

        logger.info("Ingest complete: %s", result)

        score_targets = new_post_ids + enriched_post_ids
        if score_targets:
            logger.info(
                "Triggering scoring for %d posts (%d new, %d enriched)",
                len(score_targets), len(new_post_ids), len(enriched_post_ids),
            )
            from scoring.background import score_after_ingest
            score_after_ingest(score_targets)

        return result
