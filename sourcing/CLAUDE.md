# sourcing/ - Claude Instructions

Fetches job postings from public ATS feeds and produces `RawJobPost` objects for ingestion.
Does not write to the DB, extract fields or score.

## Module structure

```
sourcing/
├── job_source.py              JobSource ABC: fetch(slug, company_name) -> list[RawJobPost]
├── job_source_adapters/       greenhouse.py, lever.py, ashby.py, comeet.py (+ jobspy.py, see below)
├── source_registry/{source}.yaml   shipped defaults per source: `enabled`, `confirmed` (demo companies), `candidates`
├── source_registry.py         load_confirmed(), load_candidates(), list_sources()
└── run_sourcing.py            batch runner CLI
```

## Running

```bash
python -m sourcing.run_sourcing                          # all enabled sources, JSON to sourcing/output/
python -m sourcing.run_sourcing --source greenhouse lever
python -m sourcing.run_sourcing --ingest                 # fetch + ingest (+ auto-scoring)
python -m sourcing.run_sourcing --source ashby --slug openai   # ad-hoc slug test, bypasses the registry
```

Output goes to `sourcing/output/{source}/{slug}.json` (gitignored). `--dry-run` skips file writes.

## Platforms

| ATS | Endpoint | Auth | Description |
|---|---|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` | none | HTML |
| Lever | `https://api.lever.co/v0/postings/{slug}?mode=json` | none | plain text |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{slug}` | none | plain text |
| Comeet | `https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}` | none | not in the API |

Greenhouse, Lever and Ashby are the public feeds behind hosted career pages: one response with
the full listing, no pagination. Comeet registry entries use `uid` + `token` (both are embedded in
the company's careers page HTML). The Comeet positions API has no description field, so Comeet
posts ingest with `raw_description = ""` and score on title and metadata; a later fetch that has a
description enriches the existing post.

### Field mapping

| RawJobPost | Greenhouse | Lever | Ashby | Comeet |
|---|---|---|---|---|
| `external_id` | `id` | `id` | `id` | `uid` |
| `title` | `title` | `text` | `title` | `name` |
| `company_name` | response | passed-in | passed-in | response |
| `location_raw` | `location.name` | `categories.location` | `locationName` | `location.name` |
| `apply_url` | `absolute_url` | `hostedUrl` | `jobUrl` | `url_active_page` |
| `raw_description` | `content` (HTML) | `descriptionPlain` | `descriptionPlain` | `""` |
| `posting_date` | `first_published` | `createdAt` (ms) | `publishedAt` | `time_updated` |

`extraction/rules.py` expects `metadata["departments"]` as a list of dicts (`[{"id", "name"}]`);
the Greenhouse adapter keeps the raw department objects for that reason. Remote-type signals:
Lever/Ashby `workplaceType`, Comeet `workplace_type`, Greenhouse an optional "Location Type"
metadata entry, otherwise keywords in the location and description.

## Slug discovery

There is no global search. Take the slug from a careers page URL (`boards.greenhouse.io/{slug}`,
`jobs.lever.co/{slug}`, `jobs.ashbyhq.com/{slug}`) or guess and test with `--slug`. Greenhouse
slugs are case-sensitive. "Not found": HTTP 404 everywhere; Lever may also return
`{"ok": false}`; an empty Ashby list can mean "no open roles".

## Registry

Which companies to fetch is per-user config: `companies.yaml` in `JOBTRACKER_HOME` (or
`resources/`), one key per source, written by the `build-profile` skill (template:
`resources/templates/companies.yaml`). When it exists, `load_confirmed()` reads only that file; a
source missing from it fetches nothing. Without it, the shipped `confirmed` lists are used: Israeli
tech companies matching the demo persona, and what `build_demo.sh` fetches.

A registry file with `enabled: false` is skipped by `list_sources()` either way (it can still be
run explicitly with `--source`). `scripts/probe_companies.py "Company"` finds a company's ATS and
slug on Greenhouse, Lever and Ashby.

`jobspy.py` (LinkedIn search via the optional `python-jobspy` package) is disabled by default
(`enabled: false` in `jobspy.yaml`).

## Adding a source

1. `sourcing/source_registry/{source}.yaml`
2. `sourcing/job_source_adapters/{source}.py` implementing `JobSource`
3. Register it in `SOURCES` in `run_sourcing.py`
