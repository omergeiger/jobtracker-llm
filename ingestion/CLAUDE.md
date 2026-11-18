# Ingestion Module — Claude Instructions

## What this module owns

Ingestion receives raw job posts from the sourcing module, deduplicates them, extracts
structured fields using rule-based logic, and persists everything to SQLite.

It does **not** fetch from any external source, score posts, or render a UI.
Those concerns live in separate modules (`sourcing/`, `scoring/`).

---

## Folder structure

```
ingestion/
├── models/
│   └── raw_job_post.py     ← contract with sourcing — do not change without coordinating
├── db/
│   ├── models.py           ← SQLAlchemy table definitions
│   ├── session.py          ← engine + SessionLocal; DB path from config_paths (JOBTRACKER_HOME)
│   └── init_db.py          ← create_all(); called automatically by IngestService
├── extraction/
│   └── rules.py            ← rule-based extraction: remote_type, office_days, seniority, department
└── pipeline.py             ← IngestService: the only public entry point for ingestion
```

The database path comes from `config_paths.DB_PATH`: `$JOBTRACKER_HOME/jobtracker.db` when the
env var is set, else `data/jobtracker.db` at the repo root. Either way it doesn't depend on the
working directory.

---

## The sourcing contract

`RawJobPost` in `models/raw_job_post.py` is the interface between sourcing and ingestion.
Sourcing produces these objects; ingestion consumes them.

```python
class RawJobPost(BaseModel):
    source_id: str        # logical source name, e.g. "greenhouse"
    external_id: str      # source's own ID for this posting
    title: str
    company_name: str
    location_raw: str     # raw string exactly as source provides — may be noisy
    apply_url: str
    raw_description: str  # HTML or plain text
    posting_date: datetime
    metadata: dict = {}   # source-specific extras (departments, offices, etc.)
```

**Do not rename or remove fields without coordinating with the sourcing module.**
Adding optional fields (with defaults) is safe.

---

## Key design decisions

**`extracted_fields` is a JSON blob, not columns.** The extraction schema will evolve.
Storing it as JSON means adding a new extracted field (e.g. `visa_sponsorship`) requires
no DB migration — just update `rules.py` and re-ingest. Raw description is always preserved
so re-extraction is possible at any time.

**Deduplication is source-aware and hash-based.** The `dedup_hash` column has a `UNIQUE`
constraint. The key differs by source:
- **LinkedIn** (`source_id == "linkedin"`): SHA-256 of `company_name:title:location_raw`.
  LinkedIn reassigns job IDs on every repost, making the URL unstable; `location_raw` is
  the only stable semantic anchor.
- **All other sources**: SHA-256 of `company_name:title:normalize_url(apply_url)` — the
  normalized ATS URL encodes a stable job ID.
Semantic deduplication (catching reposts with slightly different titles) is deferred.

**One DB transaction per batch.** `IngestService.ingest()` commits all posts in a single
transaction. A single bad post logs an error and is skipped; the rest of the batch commits.

**`Company` is a thin stub.** Only `name` and `domain` are stored. Full enrichment
(funding, headcount, stage) is deferred. Do not add enrichment logic here.

**No LLM calls in this module.** Extraction is entirely rule-based. LLM-based extraction,
if ever added, belongs in `extraction/`.

---

## DB schema summary

| Table | Purpose |
|---|---|
| `sources` | One row per data source; tracks last fetch time and errors |
| `companies` | Thin stub — created on first encounter, one row per unique company name |
| `job_posts` | Main table; `status` starts as `new`; `score` and `score_breakdown` start null |
| `user_actions` | Append-only log of user interactions (saved, applied, dismissed, noted) |

`status` valid values: `new`, `reviewed`, `applied`, `rejected`, `archived`, `closed`
(`closed` is written by `scripts/check_closed_postings.py`, not by this module — see root CLAUDE.md)
`user_actions.action` valid values: `saved`, `applied`, `dismissed`, `noted`

---

## Extraction rules (`extraction/rules.py`)

Fields extracted and their logic:

| Field | Source | Logic |
|---|---|---|
| `remote_type` | `location_raw` + description | Pattern match: hybrid > remote > onsite > unknown |
| `office_days` | description | Regex for `N days/week` patterns; only set when `remote_type == hybrid` |
| `seniority_level` | title only | Priority-ordered keywords: director > vp > principal > staff > lead > senior > junior > manager > mid |
| `department` | `metadata["departments"]` | Direct from source metadata if available; no text inference |
| `title`, `company_name`, `location_raw` | passthrough | Stored as-is from `RawJobPost` |

When adding new extracted fields:
1. Add the extractor function in `rules.py`
2. Add it to the dict returned by `extract_fields()`
3. No DB migration needed — it will appear in `extracted_fields` JSON for new posts

---

## How to run

```bash
# Install dependencies (from project root)
pip install -r requirements.txt
```

---

## Tests

`tests/test_extraction.py` (field extraction golden cases) and `tests/test_ingest_recall.py`
(every input post is ingested, a duplicate, enriched or an error; re-ingest is idempotent).
