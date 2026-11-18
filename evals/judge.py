"""LLM-as-judge evals (C5, C6). Calls Claude via the headless CLI (subscription auth).

    python -m evals.judge --name full                      # new run folder, both evals
    python -m evals.judge --only relevance --run-dir evals/results/runs/<run>   # add to / resume a run
    python -m evals.judge --only faithfulness --score-with haiku --name faithfulness_haiku
        # score the C5 sample with Claude Haiku (saved to the run folder, never to the DB),
        # then judge those breakdowns instead of the stored Ollama ones
    python -m evals.judge --only relevance --score-with haiku --name c4_haiku
        # also score the labeled posts with Claude Haiku: an extra system in the C4 table

Calls run in parallel (--workers, default 5); each failed call is retried once, then
logged and left out (the report shows how many posts each scorer has).

C6 relevance: the judge rates each human-labeled post 1-5 for the persona, blind to all
scores. Agreement with the human labels (computed in evals.run) validates the judge.

C5 faithfulness: the judge checks a scorer's breakdown against the post text: is every
claim in summary / domain / location / role supported by the post? It also rates whether the
score is reasonable for the profile (1-5). Records carry the scorer (engine) they grade.

Results go to <run-dir>/judge_relevance.jsonl and judge_faithfulness.jsonl, and parameters to
<run-dir>/config.json. A record is reused when its (apply_url, judge model, scorer) is already
in the run folder, so an interrupted run resumes without repeating calls.
"""

import argparse
import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from evals.common import (
    LABELS_PATH, RUBRIC, append_jsonl, claude_json, load_labels, load_posts, new_run_dir, post_text, read_jsonl,
    record_step,
)
from scoring.engines.claude_engine import ClaudeEngine
from scoring.profile import profile_as_text

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

RELEVANCE_FILE = "judge_relevance.jsonl"
FAITHFULNESS_FILE = "judge_faithfulness.jsonl"
DEFAULT_WORKERS = 5

RELEVANCE_SYSTEM = """\
You judge how relevant a job posting is for one specific candidate, as the candidate would.
Use only the candidate profile and the posting. Rate on this scale:
{rubric}

=== CANDIDATE PROFILE ===
{profile}"""

RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "reason": {"type": "string"},
    },
    "required": ["label", "reason"],
}

FAITHFULNESS_SYSTEM = """\
You audit an automated job-post scorer. You get a candidate profile, a job posting and the
scorer's explanation. Check every factual claim the explanation makes ABOUT THE POSTING
(technologies, domain, location, work mode, seniority, team, company) against the posting
text. A claim is unsupported if the posting doesn't say it or contradicts it. Judgments about
fit are not factual claims; only check what is asserted about the posting.
Then rate whether the numeric score is reasonable for this profile: 1 = clearly wrong,
3 = debatable, 5 = clearly right.

=== CANDIDATE PROFILE ===
{profile}"""

FAITHFULNESS_SCHEMA = {
    "type": "object",
    "properties": {
        "faithful": {"type": "boolean"},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "score_reasonable": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    "required": ["faithful", "unsupported_claims", "score_reasonable"],
}


def _done_keys(path: Path, *fields: str) -> set[tuple]:
    return {tuple(r.get(f) for f in fields) for r in read_jsonl(path)}


def _parallel(items: list, fn, workers: int):
    """Yield (n_finished, item, result, error) as calls finish; each call is retried once."""
    def call(item):
        error = None
        for _ in range(2):
            try:
                return fn(item), None
            except Exception as exc:   # noqa: BLE001 - recorded, never silently dropped
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
        return None, error

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(call, item): item for item in items}
        for n, fut in enumerate(as_completed(futures), 1):
            result, error = fut.result()
            yield n, futures[fut], result, error


def judge_relevance(run_dir: Path, posts: list[dict], model: str, workers: int) -> None:
    labels = load_labels()
    if not labels:
        logger.info("No labels yet (%s): skipping relevance judge.", LABELS_PATH)
        return
    path = run_dir / RELEVANCE_FILE
    done = _done_keys(path, "apply_url", "judge_model")
    system = RELEVANCE_SYSTEM.format(profile=profile_as_text(), rubric=RUBRIC)
    todo = [p for p in posts if p["apply_url"] in labels and (p["apply_url"], model) not in done]
    logger.info("Relevance judge: %d to do, %d done", len(todo), len(done))
    t0, errors = time.time(), []
    for n, p, out, err in _parallel(todo, lambda p: claude_json(system, post_text(p), RELEVANCE_SCHEMA, model=model), workers):
        if err:
            errors.append({"apply_url": p["apply_url"], "error": err})
            logger.warning("  [%d/%d] %s -> ERROR %s", n, len(todo), p["title"][:50], err)
            continue
        append_jsonl(path, {"apply_url": p["apply_url"], "judge_model": model, **out})
        logger.info("  [%d/%d] %s -> %s", n, len(todo), p["title"][:50], out.get("label"))
    record_step(run_dir, "judge_relevance", judge_model=model, n_labeled=len(labels), n_todo=len(todo),
                workers=workers, wall_s=round(time.time() - t0, 1), errors=errors)


def faithfulness_sample(posts: list[dict], n: int, seed: int) -> list[dict]:
    """Deterministic sample of Ollama-scored posts (the same posts for every scorer compared)."""
    llm_scored = sorted(
        (p for p in posts if p["breakdown"] and str(p["breakdown"].get("engine", "")).startswith("ollama")),
        key=lambda p: p["apply_url"],
    )
    return random.Random(seed).sample(llm_scored, min(n, len(llm_scored)))


def score_with_claude(run_dir: Path, sample: list[dict], model: str, workers: int) -> list[tuple[dict, dict]]:
    """Score the sample with ClaudeEngine(model). Saved to the run folder only, never to the DB."""
    scorer = f"claude/{model}"
    path = run_dir / f"scores_claude-{model}.jsonl"
    done = {r["apply_url"] for r in read_jsonl(path)}
    todo = [p for p in sample if p["apply_url"] not in done]
    logger.info("Scoring with %s: %d to do, %d done", scorer, len(todo), len(done))

    def score(p):
        engine = ClaudeEngine(model=model)   # one per call: it keeps per-call cost/timing state
        result = engine.score(p["post_dict"])
        return result.to_dict(), engine.last_cost_usd, engine.last_duration_ms

    t0, errors = time.time(), []
    for n, p, out, err in _parallel(todo, score, workers):
        if err:
            errors.append({"apply_url": p["apply_url"], "error": err})
            logger.warning("  [%d/%d] %s -> ERROR %s", n, len(todo), p["title"][:50], err)
            continue
        breakdown, cost, duration_ms = out
        append_jsonl(path, {"apply_url": p["apply_url"], "scorer": scorer, "breakdown": breakdown,
                            "cost_usd": cost, "duration_ms": duration_ms})
        logger.info("  [%d/%d] %s -> %s", n, len(todo), p["title"][:50], breakdown["score"])
    records = read_jsonl(path)
    record_step(run_dir, "score", scorer=scorer, n=len(sample), workers=workers,
                wall_s=round(time.time() - t0, 1), errors=errors,
                total_cost_usd=round(sum(r["cost_usd"] or 0 for r in records), 4),
                note="Same prompt as the Ollama engine (scoring/engines/llm_prompt.py); not written to the DB.")
    by_url = {r["apply_url"]: r["breakdown"] for r in records}
    return [(p, by_url[p["apply_url"]]) for p in sample if p["apply_url"] in by_url]


def judge_faithfulness(run_dir: Path, items: list[tuple[dict, dict]], model: str, workers: int,
                       scorer_note: str, seed: int) -> None:
    """Grade each (post, breakdown) pair against the post text."""
    path = run_dir / FAITHFULNESS_FILE
    done = _done_keys(path, "apply_url", "judge_model", "scorer")
    system = FAITHFULNESS_SYSTEM.format(profile=profile_as_text())
    todo = [(p, b) for p, b in items if (p["apply_url"], model, b.get("engine")) not in done]
    logger.info("Faithfulness judge: %d to do", len(todo))
    t0, errors = time.time(), []
    for n, (p, b), out, err in _parallel(todo, lambda pb: judge_one_breakdown(system, pb[0], pb[1], model), workers):
        if err:
            errors.append({"apply_url": p["apply_url"], "error": err})
            logger.warning("  [%d/%d] %s -> ERROR %s", n, len(todo), p["title"][:50], err)
            continue
        append_jsonl(path, {"apply_url": p["apply_url"], "judge_model": model,
                            "scorer": b.get("engine"), "score": b.get("score"), **out})
        logger.info("  [%d/%d] %s -> faithful=%s", n, len(todo), p["title"][:50], out.get("faithful"))
    record_step(run_dir, "judge_faithfulness", judge_model=model, n=len(items), seed=seed, scorer=scorer_note,
                apply_urls=[p["apply_url"] for p, _ in items], workers=workers,
                wall_s=round(time.time() - t0, 1), errors=errors)


def judge_one_breakdown(system: str, post: dict, breakdown: dict, model: str) -> dict:
    explanation = json.dumps({k: breakdown.get(k) for k in (
        "score", "summary", "domain_match", "location_match", "role_match", "bonus_signals",
        "dealbreaker", "dealbreaker_reason")}, ensure_ascii=False, indent=1)
    msg = f"=== JOB POSTING ===\n{post_text(post)}\n\n=== SCORER OUTPUT ===\n{explanation}"
    return claude_json(system, msg, FAITHFULNESS_SCHEMA, model=model)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LLM-as-judge evals.")
    parser.add_argument("--name", default="judge", help="name suffix for a new run folder")
    parser.add_argument("--run-dir", type=Path, help="existing run folder to add to / resume")
    parser.add_argument("--model", default="sonnet", help="judge model")
    parser.add_argument("--only", choices=["relevance", "faithfulness"])
    parser.add_argument("--n", type=int, default=20, help="faithfulness sample size")
    parser.add_argument("--seed", type=int, default=11, help="faithfulness sample seed")
    parser.add_argument("--score-with", metavar="CLAUDE_MODEL",
                        help="score with this Claude model: the labeled posts (an extra C4 system) "
                             "and the faithfulness sample (judged instead of the stored Ollama "
                             "breakdowns)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="parallel Claude calls")
    args = parser.parse_args()

    run_dir = args.run_dir or new_run_dir(args.name)
    logger.info("Run folder: %s", run_dir)
    posts = load_posts()
    if args.only != "faithfulness":
        judge_relevance(run_dir, posts, args.model, args.workers)
        if args.score_with:
            labels = load_labels()
            score_with_claude(run_dir, [p for p in posts if p["apply_url"] in labels],
                              args.score_with, args.workers)
    if args.only != "relevance":
        sample = faithfulness_sample(posts, args.n, args.seed)
        if args.score_with:
            items = score_with_claude(run_dir, sample, args.score_with, args.workers)
            note = f"claude/{args.score_with} breakdowns scored in this run"
        else:
            items = [(p, p["breakdown"]) for p in sample]
            note = "stored DB breakdown (ollama)"
        judge_faithfulness(run_dir, items, args.model, args.workers, note, args.seed)


if __name__ == "__main__":
    main()
