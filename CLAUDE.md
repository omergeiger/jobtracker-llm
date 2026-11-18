# JobPostTracker - Claude Instructions

Local job-feed tool: fetches postings from public ATS feeds, extracts structured fields,
scores every post against a candidate profile, reranks the best ones with an LLM, and shows
the feed in a Streamlit UI.

**North star: recall first, then ranking.** Never silently drop a post. The data set is
hundreds of posts, not millions: keep everything simple; complicate only with a clear reason.

**No paid API keys.** LLM usage is local Ollama, the HuggingFace free tier, or the Claude Code
CLI in headless mode (`claude -p`, billed to the caller's existing Claude subscription login;
see `scoring/engines/claude_engine.py`). The rules engine is always the fallback.

**No preferences in code.** Everything about the candidate comes from the per-user files
(below). Never hardcode locations, domains, seniority or keywords in engine logic.

---

## Module map

```
sourcing/        JobSource adapters + runner: fetch RawJobPost from ATS feeds
ingestion/       dedup, rule-based extraction, SQLite persistence
scoring/         RulesEngine, OllamaEngine, HuggingFaceEngine, ClaudeEngine, listwise reranker
ui/              Streamlit feed, filters, post detail, user actions
evals/           eval harness: metrics, blind label sheet, LLM judge, report; results/runs/ = experiment records
tests/           pytest: extraction, rules engine (+ golden snapshot), ingest recall, reranker validation, metrics
examples/        fictional personas (CV PDF, profile, rules, scored DB): dana_example (browsing demo),
                 israel_israeli (main eval, blind owner labels)
resources/       per-user files (gitignored) + templates/ (committed)
scripts/         demo build, daily pipeline, email report, closed-posting check, profile check, ATS slug probe
.claude/skills/build-profile/   CV PDF -> per-user profile files
config_paths.py  where per-user state lives (JOBTRACKER_HOME)
```

Each module has its own `CLAUDE.md` with contracts and design notes. Read it before changing
that module.

## Per-user state (`config_paths.py`)

One env var, `JOBTRACKER_HOME`, points at a folder with these files:

| File | Read by |
|---|---|
| `user_profile.yaml` | LLM engines (pasted into the system prompt) |
| `scoring_rules.yaml` | RulesEngine (keywords, labels, points) |
| `cv_structured.json` | LLM engines (CV summary) |
| `companies.yaml` | sourcing: companies to fetch (optional; without it, the shipped demo registry) |
| `jobtracker.db` | everything |

Unset: `resources/` for the config files and `data/` for the DB. The demo is
`JOBTRACKER_HOME=examples/dana_example`. Create your own files with the `build-profile` skill;
validate them with `python scripts/check_profile.py`.

## Module boundaries

| Module | Owns | Does NOT own |
|---|---|---|
| `sourcing/` | Fetching from external APIs; producing `RawJobPost` | DB writes, extraction, scoring |
| `ingestion/` | Dedup, rule-based extraction, DB persistence | Fetching, scoring, UI |
| `scoring/` | Reading `job_posts`, writing `score`, `score_breakdown`, rerank columns | Fetching, extraction, UI |
| `ui/` | Reading the DB, rendering, recording user actions | Writes beyond user actions/ratings/tags/status |
| `evals/` | Measuring the above; never writes to the DB | Product behavior |

## Cross-module contract: `RawJobPost`

`ingestion/models/raw_job_post.py` is the interface between sourcing and ingestion.
Don't rename or remove fields without updating both sides.

```python
class RawJobPost(BaseModel):
    source_id: str        # e.g. "greenhouse", "lever", "ashby", "comeet"
    external_id: str      # source's own ID
    title: str
    company_name: str
    location_raw: str     # raw string as the source provides it
    apply_url: str
    raw_description: str  # HTML or plain text, always preserved
    posting_date: datetime
    metadata: dict = {}   # source-specific extras
```

## Database

SQLite, SQLAlchemy 2.0; `ingestion/db/session.py` owns the engine. No Alembic:
`create_all()` is enough until the schema stabilizes.

| Table | Purpose |
|---|---|
| `sources` | One row per data source; last fetch time and errors |
| `companies` | Thin stub: name + domain |
| `job_posts` | Main table; `score`, `score_breakdown` start null |
| `user_actions` | Append-only log: `saved`, `applied`, `dismissed`, `noted`, `rated`, `unrated`, `tagged`, `untagged` |

`job_posts` notes:
- `status`: `new`, `reviewed`, `applied`, `rejected`, `archived`, `closed` (`closed` = gone at the
  source, set by `scripts/check_closed_postings.py`). Status only moves forward from `new`.
- `extracted_fields` is a JSON blob: adding a field needs no migration.
- `global_rerank_position` / `rerank_rationale`: written by the reranker; lower = better.
- `user_rating` (1-5) and `feedback_tags` (JSON list): set in the UI. Rating or tagging a `new`
  post advances it to `reviewed`; clearing never reverts status.
- Dedup: SHA-256 of `company:title:normalized_apply_url` (`UNIQUE`).

## Scoring engines

- **RulesEngine**: keyword scoring from `scoring_rules.yaml`, ~0 ms/post: domain + location +
  seniority + bonus - penalty, with title and dealbreaker early exits. Runs right after ingest.
- **OllamaEngine**: local LLM (`qwen2.5:7b`, falls back to `llama3.2`). Prompt = profile +
  `scoring/scoring_guide.md` + CV summary. Runs in the background after ingest.
- **HuggingFaceEngine**: same prompt via the HF free-tier router (`--engine hf`).
- **ClaudeEngine**: same prompt via `claude -p` (default `haiku`; `--engine claude`). Not in the
  auto fallback chain.
- **Listwise reranker** (`scoring/reranker.py`): Claude ranks recent posts scoring >= 80 against
  each other; output is validated (every post exactly once) and retried on failure.

```bash
python -m scoring.service                     # auto engine (Ollama, else rules), unscored posts
python -m scoring.service --engine rules --rescore
python -m scoring.service --engine claude --rescore --min-score 70
python -m scoring.reranker
```

## Evals

```bash
pytest                                        # C1-C3: deterministic, offline
python -m evals.judge --name <run>            # C5/C6 LLM judge -> evals/results/runs/<ts>_<run>/
python -m evals.run --run-dir <run folder>    # report.md for that run (+ copy at evals/results/report.md)
```

Every experiment run folder holds `config.json` (models, seeds, sample, git commit, data
fingerprints), raw JSONL and `report.md`. Never edit past run folders; start a new run.
Rules-engine changes that alter scores fail the golden snapshot on purpose: regenerate it
deliberately with `UPDATE_SNAPSHOT=1 pytest tests/test_rules_engine.py`.

## Running

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
JOBTRACKER_HOME=examples/dana_example streamlit run ui/app.py     # demo
python -m sourcing.run_sourcing --ingest                  # fetch + ingest + score (your own profile)
./scripts/build_demo.sh                                   # rebuild the demo DB
./scripts/run_pipeline.sh --dry-run                       # daily pipeline without sending email
```
