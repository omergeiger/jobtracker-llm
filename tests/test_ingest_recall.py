"""C2 - recall invariant: ingestion accounts for every post it is given (verifiable).

North star: never silently drop a post. Every input post must end up as exactly one of
ingested / duplicate / enriched / error, and every stored post must get a score.
Runs against a throwaway DB in the test JOBTRACKER_HOME (see conftest.py); Ollama
background scoring is disabled so the test is offline and deterministic.
"""

from datetime import datetime, timezone

import pytest

import scoring.background
from ingestion.db import JobPost, SessionLocal
from ingestion.models import RawJobPost
from ingestion.pipeline import IngestService


@pytest.fixture(autouse=True)
def no_ollama(monkeypatch):
    monkeypatch.setattr(scoring.background, "launch_ollama_background", lambda ids: None)


def raw(company, title, url, desc="<p>Backend services in Go.</p>", source="greenhouse"):
    return RawJobPost(
        source_id=source, external_id=f"{company}-{title}", title=title, company_name=company,
        location_raw="Tel Aviv, Israel", apply_url=url, raw_description=desc,
        posting_date=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )


def accounted(result):
    return result.ingested + result.skipped_duplicates + result.enriched + len(result.errors)


def test_every_post_is_accounted_for_and_scored():
    batch = [raw("AcmeRecall", f"Engineer {i}", f"https://jobs.example.com/acme/{i}") for i in range(5)]
    # Same job with tracking params -> same normalized URL -> duplicate, not a new post
    batch += [raw("AcmeRecall", "Engineer 0", "https://jobs.example.com/acme/0?utm_source=x"),
              raw("acmerecall", "ENGINEER 1", "https://jobs.example.com/acme/1#apply")]

    result = IngestService().ingest(batch)

    assert accounted(result) == len(batch)
    assert (result.ingested, result.skipped_duplicates, result.errors) == (5, 2, [])

    with SessionLocal() as s:
        stored = s.query(JobPost).filter(JobPost.title.like("Engineer %")).all()
        assert len(stored) == 5
        assert all(p.score is not None and p.score_breakdown["engine"] == "rules" for p in stored)


def test_reingest_is_idempotent():
    batch = [raw("AcmeIdem", f"Dev {i}", f"https://jobs.example.com/idem/{i}") for i in range(3)]
    first = IngestService().ingest(batch)
    second = IngestService().ingest(batch)
    assert first.ingested == 3
    assert (second.ingested, second.skipped_duplicates) == (0, 3)
    assert accounted(second) == len(batch)


def test_empty_description_is_enriched_not_dropped():
    url = "https://jobs.example.com/enrich/1"
    IngestService().ingest([raw("AcmeEnrich", "Platform Engineer", url, desc="")])
    result = IngestService().ingest([raw("AcmeEnrich", "Platform Engineer", url, desc="<p>Kubernetes.</p>")])
    assert result.enriched == 1 and accounted(result) == 1
    with SessionLocal() as s:
        post = s.query(JobPost).filter_by(apply_url=url).one()
        assert "Kubernetes" in post.raw_description
