"""Shared helpers for the evals: data access, labels, experiment run folders, and the Claude call.

Evals run against the bundled demo (examples/dana_example) unless JOBTRACKER_HOME is already set.

Every experiment lives in its own folder, evals/results/runs/<YYYYmmdd_HHMMSS>_<name>/, holding
config.json (what ran: models, seeds, sample sizes, git commit, data fingerprints, one entry per
step), the raw JSONL results, and report.md. Folders are committed, so they are the record.
"""

import csv
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("JOBTRACKER_HOME", str(ROOT / "examples" / "dana_example"))

import config_paths  # noqa: E402  (must follow the JOBTRACKER_HOME default)

logger = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).resolve().parent
# One labeled set per persona: the JOBTRACKER_HOME folder name (examples/dana_example -> "dana_example")
PERSONA = config_paths.HOME.name if config_paths.HOME else "default"
DATA_DIR = EVALS_DIR / "data" / PERSONA
LABELS_PATH = DATA_DIR / "labels.csv"
RESULTS_DIR = EVALS_DIR / "results"
RUNS_DIR = RESULTS_DIR / "runs"
REVIEW_THRESHOLD = 50   # "worth reviewing" per the LLM scoring scale

# Shared by the human labeling sheet and the LLM judge, so both rate on the same scale.
RUBRIC = """\
5 = Perfect match: the candidate would apply right away (target role, level, location and work mode).
4 = Good fit: would likely apply; right role with one minor gap (e.g. an extra office day, adjacent domain, stretch level).
3 = Borderline: might apply; mixed fit (e.g. right role but weak location, or right location but off-center role).
2 = Probably not: mostly wrong (wrong level, mostly a different function, location hard to work with).
1 = Irrelevant: would never apply (a different function, a dealbreaker, on-site abroad)."""
LABELS = {1, 2, 3, 4, 5}
# Who produced each persona's reference labels in evals/data/<persona>/labels.csv (shown in the report).
LABEL_SOURCES = {
    "dana_example": ("Gemini (via the Gemini UI, a different model family from the scorers and the Claude judge), "
             "given the persona profile, the rubric and the full posting text; accepted by the repo owner "
             "without edits. Sample: stratified by the stored Ollama score + 10 from the reranked set"),
    "israel_israeli": "the repo owner, by hand, blind to all scores",
}
LABEL_SOURCE = LABEL_SOURCES.get(PERSONA, "not recorded (add it to LABEL_SOURCES in evals/common.py)")


# ── Experiment run folders ─────────────────────────────────────────────────────

def new_run_dir(name: str) -> Path:
    run_dir = RUNS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{name}"
    run_dir.mkdir(parents=True)
    return run_dir


def latest_run_dir() -> Path | None:
    runs = sorted(d for d in RUNS_DIR.glob("*") if d.is_dir()) if RUNS_DIR.exists() else []
    return runs[-1] if runs else None


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else None


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def record_step(run_dir: Path, step: str, **params) -> None:
    """Append one step's parameters and provenance to run_dir/config.json."""
    path = run_dir / "config.json"
    config = json.loads(path.read_text()) if path.exists() else {"run": run_dir.name, "steps": []}
    config["steps"].append({
        "step": step,
        "at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git("rev-parse", "--short", "HEAD"),
        # Uncommitted code changes only; eval inputs/outputs (labels, run folders) don't count
        "git_dirty": bool(_git("status", "--porcelain", "--", "evals", "scoring", "ingestion", "resources/templates",
                               "examples", ":!evals/results", ":!evals/data")),
        "db": str(Path(config_paths.DB_PATH).relative_to(ROOT)) if str(config_paths.DB_PATH).startswith(str(ROOT)) else str(config_paths.DB_PATH),
        "db_sha256": _sha256(Path(config_paths.DB_PATH)),
        "persona": PERSONA,
        "labels_sha256": _sha256(LABELS_PATH),
        **params,
    })
    path.write_text(json.dumps(config, indent=2) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Data access ────────────────────────────────────────────────────────────────

def load_posts() -> list[dict]:
    """All posts in the DB, read-only, with the fields the evals need."""
    con = sqlite3.connect(f"file:{config_paths.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select id, title, apply_url, raw_description, extracted_fields, score, score_breakdown, "
        "global_rerank_position, rerank_rationale, status from job_posts"
    ).fetchall()
    posts = []
    for r in rows:
        ef = json.loads(r["extracted_fields"] or "{}")
        posts.append({
            "id": r["id"],
            "title": r["title"],
            "apply_url": r["apply_url"],
            "company": ef.get("company_name", ""),
            "location": ef.get("location_raw", ""),
            "description": (ef.get("description_text") or "").strip(),
            "score": r["score"],
            "breakdown": json.loads(r["score_breakdown"]) if r["score_breakdown"] else None,
            "rerank_position": r["global_rerank_position"],
            "rerank_rationale": r["rerank_rationale"],
            "status": r["status"],
            # The dict shape the scoring engines take (see scoring/service.py)
            "post_dict": {
                "title": r["title"],
                "company_name": ef.get("company_name", ""),
                "location_raw": ef.get("location_raw", ""),
                "remote_type": ef.get("remote_type", "unknown"),
                "office_days": ef.get("office_days"),
                "seniority_level": ef.get("seniority_level", "mid"),
                "department": ef.get("department"),
                "raw_description": r["raw_description"] or "",
            },
        })
    return posts


def load_labels() -> dict[str, int]:
    """apply_url -> human label (1-5), only for rows that have been labeled."""
    if not LABELS_PATH.exists():
        return {}
    with LABELS_PATH.open() as f:
        return {
            row["apply_url"]: int(row["label"])
            for row in csv.DictReader(f)
            if row.get("label", "").strip() in {str(v) for v in LABELS}
        }


def post_text(post: dict, max_chars: int = 3000) -> str:
    return (
        f"Title: {post['title']}\nCompany: {post['company']}\nLocation: {post['location']}\n\n"
        f"Description:\n{post['description'][:max_chars]}"
    )


def claude_json(system_prompt: str, user_msg: str, schema: dict, model: str = "sonnet",
                timeout_s: int = 180) -> dict:
    """One isolated headless Claude Code call (subscription auth) returning schema-shaped JSON."""
    env = dict(os.environ)
    if env.pop("ANTHROPIC_API_KEY", None):
        logger.warning("ANTHROPIC_API_KEY was set; stripping it so the claude CLI uses the subscription login")
    result = subprocess.run(
        [
            "claude", "-p", user_msg,
            "--system-prompt", system_prompt,
            "--model", model,
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--tools", "",
            "--no-session-persistence",
            "--setting-sources", "",
            "--strict-mcp-config",
        ],
        capture_output=True, text=True, cwd=tempfile.gettempdir(), timeout=timeout_s, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr[:300]}")
    data = json.loads(result.stdout)
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(data.get('result', ''))[:300]}")
    return data.get("structured_output") or {}
