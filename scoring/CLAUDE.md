# Scoring Module - Claude Instructions

## What this module owns

Scoring reads job posts from the DB, evaluates each against the candidate's per-user files
(`user_profile.yaml`, `scoring_rules.yaml`, `cv_structured.json`; see `config_paths.py`) and
writes `job_posts.score` / `job_posts.score_breakdown`. The reranker also writes
`global_rerank_position` / `rerank_rationale`. It does not fetch, extract or render.

## Folder structure

```
scoring/
├── profile.py            loads the per-user files (paths from config_paths / JOBTRACKER_HOME)
├── service.py            ScoringService + CLI entry point
├── reranker.py           listwise Claude reranker
├── background.py         score_after_ingest(): rules sync, then Ollama in a background thread
├── scoring_guide.md      generic guide for how LLM engines interpret any profile
├── engines/
│   ├── base.py           ScoreResult dataclass + ScoringEngine ABC
│   ├── llm_prompt.py     shared system prompt for all LLM engines
│   ├── rules.py          RulesEngine, driven entirely by scoring_rules.yaml
│   ├── ollama_engine.py  local LLM via Ollama
│   ├── huggingface.py    remote LLM via the HF free-tier router
│   └── claude_engine.py  headless Claude Code CLI (`claude -p`), subscription auth
└── ad_hoc/               analysis scripts, never write to the DB
    ├── compare_engines.py     diff Ollama vs rules scores
    ├── runtime_benchmark.py   time an engine on N posts
    └── score_histogram.py     score distribution by engine
```

## Engine input

```python
{
    "title": str, "company_name": str, "location_raw": str,
    "remote_type": str,         # "remote" | "hybrid" | "onsite" | "unknown"
    "office_days": str | None,  # e.g. "2 days/week"; only set when hybrid
    "seniority_level": str,     # director | vp | principal | staff | manager | lead | senior | mid | junior
    "department": str | None,
    "raw_description": str,     # HTML or plain text
}
```

## Engines

**RulesEngine** (always available, ~0 ms/post). The structure is fixed; every keyword, label and
point value comes from `scoring_rules.yaml` (template: `resources/templates/scoring_rules.yaml`):
- Early exits on the title: hard exclusion regex (dealbreaker), soft-excluded functions (fixed score).
- Dealbreakers on the full text: `any_of` keywords unless `unless_any_of` keywords appear.
- Score = domain (primary / open_to / demote / weak / none) + location (ordered tiers with
  per-work-mode rules and an office-day limit, home region, worldwide / unclear / region-restricted
  remote, elsewhere) + seniority (points per extracted level) + bonus signals (capped) - first
  matching penalty, clamped to [0, 100].
- Tests: `tests/test_rules_engine.py` (intended behavior + golden snapshot against the demo rules).

**OllamaEngine**: `qwen2.5:7b` preferred, `llama3.2:latest` fallback, `--model` to override.
System prompt = profile + `scoring_guide.md` + CV summary, built once; per post: title, company,
location, description (first 2,000 chars). `format="json"`, `temperature=0.1`. Avoid very large
models on modest hardware (a 70B model's default context can exhaust memory).

**HuggingFaceEngine**: same prompt via `router.huggingface.co` (`HF_API_TOKEN`, `HF_MODEL`,
`HF_PROVIDER` in `.env`). Retries once on HTTP 503.

**ClaudeEngine**: same prompt via `claude -p` with `--json-schema`, tools and settings disabled,
no session persistence. Strips `ANTHROPIC_API_KEY` from the subprocess env (warning when present)
so it can only use the subscription login. Default `haiku`. In the evals, Haiku scored 20 posts for
$0.03 total and its scores were judged reasonable far more often than Ollama's
(see `evals/results/runs/`).

**Auto mode** (default): Ollama, else RulesEngine. HF and Claude are explicit only.
`score_breakdown.engine` records which engine produced each score (`rules`, `ollama/<model>`,
`huggingface/<model>`, `claude/<model>`).

**Listwise reranker**: ranks posts with `score >= 80`, ingested in the last 7 days and still
active, using Claude (`haiku` by default). Posts are shown to the model under short labels
(not UUIDs); every reply is validated (each post exactly once, ranks 1..N) and retried.
New posts are placed relative to already-ranked anchor posts. Tests:
`tests/test_reranker_validation.py`.

## How to run

```bash
python -m scoring.service                       # auto engine, unscored posts
python -m scoring.service --engine rules --rescore
python -m scoring.service --engine ollama --upgrade-rules
python -m scoring.service --engine claude --rescore --min-score 70
python -m scoring.reranker [--dry-run]
```

## Score breakdown schema

```json
{
  "score": 74,
  "summary": "Strong match: primary domain (Backend / distributed systems), preferred location.",
  "domain_match": "...", "location_match": "...", "role_match": "...",
  "bonus_signals": ["..."],
  "dealbreaker": false, "dealbreaker_reason": "",
  "engine": "ollama/qwen2.5:7b"
}
```
