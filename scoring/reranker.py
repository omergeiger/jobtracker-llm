"""
Listwise Claude reranker.

Ranks the strongest recent posts against each other in a few Claude calls and
writes global_rerank_position + rerank_rationale. Larger candidate sets are
handled incrementally with anchor posts (rank_group, rank_with_anchors).

Usage (daily pipeline):
    python -m scoring.reranker [--model haiku] [--dry-run]
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup

from ingestion.db import SessionLocal, init_db, JobPost, INACTIVE_STATUSES
from logging_config import setup_logging
from scoring.engines.llm_prompt import base_system_prompt
from scoring.service import _post_to_dict

logger = logging.getLogger(__name__)

_TIMEOUT_S = 600
_MAX_ATTEMPTS = 2
_DESC_TRUNCATE = 2000
_MIN_SCORE = 80
_NEW_DAYS = 7

_RERANK_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "post_id": {"type": "string"},
                    "rank": {"type": "integer"},
                    "rationale": {"type": "string"},
                },
                "required": ["post_id", "rank", "rationale"],
            },
        },
    },
    "required": ["rankings"],
})

_TOURNAMENT_INSTRUCTIONS = """

You will be given a list of job postings, each already carrying a score (0-100) from a
simpler, single-post scoring pass. Treat that score as a noisy prior, not ground truth.

Your task: re-rank the ENTIRE list from best fit (rank 1) to worst fit (rank N) by
comparing the postings against each other. Every posting must receive a distinct rank -
no ties, no gaps, no skipped or invented postings.

Provide your answer as structured output matching the given schema."""

_INCREMENTAL_INSTRUCTIONS = """

You will be given two sets of job postings:
- ANCHOR posts: already globally ranked. Their relative ordering is ground truth -
  do NOT reshuffle them relative to each other. They provide fixed reference points.
- NEW posts: candidates that need to be placed into the global ranking.

Rank ALL posts (anchors + new) from best fit (rank 1) to worst fit (rank N). Place each
new post where it best fits relative to the anchors, but preserve the anchors' relative
order. Every posting must receive a distinct rank - no ties, no gaps.

Provide your answer as structured output matching the given schema."""


# ── Shared helpers ──────────────────────────────────────────────────────────────

def _truncate_description(raw_description: str) -> str:
    text = BeautifulSoup(raw_description or "", "html.parser").get_text(separator=" ")
    return text[:_DESC_TRUNCATE].strip()


def _format_post_block(label: str, short_id: str, post: JobPost, post_dict: dict) -> str:
    company = post_dict.get("company_name") or post.company_name
    return (
        f"[{label}] post_id: {short_id}\n"
        f"Title: {post.title}\n"
        f"Company: {company}\n"
        f"Location: {post_dict.get('location_raw', 'Unknown')}\n"
        f"Remote type: {post_dict.get('remote_type', 'unknown')}\n"
        f"Office days: {post_dict.get('office_days') or 'not specified'}\n"
        f"Seniority (extracted): {post_dict.get('seniority_level', 'unknown')}\n"
        f"Department: {post_dict.get('department') or 'unknown'}\n"
        f"Existing score (0-100): {(post.score or 0):.0f}\n\n"
        f"Job Description:\n{_truncate_description(post_dict.get('raw_description', ''))}\n"
        "---"
    )


def _invoke_claude_cli(
    model: str, system_prompt: str, user_msg: str, timeout_s: int = _TIMEOUT_S
) -> dict:
    env = dict(os.environ)
    if env.pop("ANTHROPIC_API_KEY", None):
        logger.warning(
            "ANTHROPIC_API_KEY was set in the environment; stripping it so the "
            "claude CLI can only authenticate via the subscription login"
        )
    result = subprocess.run(
        [
            "claude", "-p", user_msg,
            "--system-prompt", system_prompt,
            "--model", model,
            "--output-format", "json",
            "--json-schema", _RERANK_SCHEMA,
            "--tools", "",
            "--no-session-persistence",
            "--setting-sources", "",
            "--strict-mcp-config",
        ],
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
        timeout=timeout_s,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr[:300]}")
    data = json.loads(result.stdout)
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(data.get('result', ''))[:300]}")
    return data


def _map_short_ids(rankings: list, id_map: dict[str, str]) -> list:
    """
    Posts are shown to the model under short labels (A1, N7, P3) instead of UUIDs:
    36-char UUIDs made the model drop entries and transpose characters. Translate
    the labels back to real post ids; unknown labels are left as-is so validation
    reports them.
    """
    if not isinstance(rankings, list):
        return rankings
    mapped = []
    for r in rankings:
        label = str(r.get("post_id", "")).strip().strip("[]").upper()
        mapped.append({**r, "post_id": id_map.get(label, r.get("post_id"))})
    return mapped


def _validate_rankings(rankings: list, expected_ids: set[str]) -> None:
    if not isinstance(rankings, list) or len(rankings) != len(expected_ids):
        raise ValueError(
            f"expected {len(expected_ids)} ranking entries, got "
            f"{len(rankings) if isinstance(rankings, list) else type(rankings)}"
        )
    ids = {r.get("post_id") for r in rankings}
    if ids != expected_ids:
        raise ValueError(
            f"post_id mismatch - missing={expected_ids - ids}, extra={ids - expected_ids}"
        )
    ranks = {r.get("rank") for r in rankings}
    if ranks != set(range(1, len(expected_ids) + 1)):
        raise ValueError(
            f"rank set invalid - expected 1..{len(expected_ids)}, got "
            f"{sorted(r for r in ranks if r is not None)}"
        )


def _invoke_and_validate(
    model: str,
    system_prompt: str,
    user_msg: str,
    id_map: dict[str, str],
    max_attempts: int,
) -> list[dict]:
    """id_map: short label shown to the model -> real post id."""
    expected_ids = set(id_map.values())
    last_error: Exception | None = None
    data = None
    for attempt in range(1, max_attempts + 1):
        t0 = time.perf_counter()
        try:
            data = _invoke_claude_cli(model, system_prompt, user_msg)
            structured = data.get("structured_output") or {}
            rankings = structured.get("rankings")
            rankings = _map_short_ids(rankings, id_map)
            _validate_rankings(rankings, expected_ids)
            wall_s = time.perf_counter() - t0
            logger.debug(
                "rank call OK: attempt=%d cost=$%.4f duration=%dms wall=%.1fs",
                attempt,
                data.get("total_cost_usd", 0),
                data.get("duration_ms", 0),
                wall_s,
            )
            return rankings
        except Exception as exc:
            last_error = exc
            wall_s = time.perf_counter() - t0
            logger.warning("rank call attempt %d failed (%.1fs): %s", attempt, wall_s, exc)
    raise RuntimeError(
        f"rank call failed after {max_attempts} attempts: {last_error}"
    ) from last_error


# ── Public ranking functions ────────────────────────────────────────────────────

def rank_group(
    posts: list[JobPost],
    post_dicts: dict[str, dict],
    model: str,
    max_attempts: int = _MAX_ATTEMPTS,
) -> list[dict]:
    """Rank a group of posts with no anchors. Used by the tournament seeder."""
    system_prompt = base_system_prompt() + _TOURNAMENT_INSTRUCTIONS
    n = len(posts)
    id_map = {f"P{i}": post.id for i, post in enumerate(posts, 1)}
    blocks = [
        _format_post_block(str(i), f"P{i}", post, post_dicts[post.id])
        for i, post in enumerate(posts, 1)
    ]
    user_msg = (
        f"Here are {n} candidate job postings to re-rank:\n\n"
        + "\n\n".join(blocks)
        + f"\n\nReturn a strict ranking of all {n} postings above by post_id "
        f"(P1..P{n}), from best fit (rank 1) to worst fit (rank {n})."
    )
    return _invoke_and_validate(model, system_prompt, user_msg, id_map, max_attempts)


def rank_with_anchors(
    new_posts: list[JobPost],
    anchor_posts: list[JobPost],
    anchor_global_positions: dict[str, int],
    post_dicts: dict[str, dict],
    model: str,
    max_attempts: int = _MAX_ATTEMPTS,
) -> list[dict]:
    """
    Rank new posts relative to anchor posts that have known global positions.
    Returns rankings for all posts (new + anchors).
    Anchors are presented in global-position order and Claude is instructed
    not to reshuffle their relative ordering.
    """
    if not anchor_posts:
        return rank_group(new_posts, post_dicts, model, max_attempts)

    system_prompt = base_system_prompt() + _INCREMENTAL_INSTRUCTIONS
    anchors_sorted = sorted(anchor_posts, key=lambda p: anchor_global_positions[p.id])

    id_map: dict[str, str] = {}
    anchor_blocks = []
    for i, p in enumerate(anchors_sorted, 1):
        id_map[f"A{i}"] = p.id
        anchor_blocks.append(_format_post_block(
            f"ANCHOR {i} (global rank {anchor_global_positions[p.id]})",
            f"A{i}", p, post_dicts[p.id],
        ))
    new_blocks = []
    for i, p in enumerate(new_posts, 1):
        id_map[f"N{i}"] = p.id
        new_blocks.append(_format_post_block(f"NEW {i}", f"N{i}", p, post_dicts[p.id]))

    n_anchor, n_new = len(anchor_posts), len(new_posts)
    n_total = n_anchor + n_new
    user_msg = (
        f"ANCHOR POSTS (relative order is fixed - do not reshuffle among themselves):\n\n"
        + "\n\n".join(anchor_blocks)
        + f"\n\nNEW CANDIDATE POSTS (rank these relative to the anchors):\n\n"
        + "\n\n".join(new_blocks)
        + f"\n\nReturn a strict ranking of all {n_total} postings by post_id: "
        f"A1..A{n_anchor} and N1..N{n_new}, every one exactly once, "
        f"from best fit (rank 1) to worst fit (rank {n_total})."
    )
    return _invoke_and_validate(model, system_prompt, user_msg, id_map, max_attempts)


# ── Incremental position assignment ────────────────────────────────────────────

def assign_incremental_positions(
    combined_rankings: list[dict],
    new_post_ids: set[str],
    anchor_global_positions: dict[str, int],
) -> dict[str, tuple[float, str]]:
    """
    Given Claude's combined ranking (new posts + anchors), assign fractional global
    positions to new posts by interpolating between surrounding anchors' known positions.
    Returns {post_id: (fractional_global_position, rationale)} for new posts only.
    """
    ranked = sorted(combined_rankings, key=lambda x: x["rank"])
    calibration = [
        (item["rank"], anchor_global_positions[item["post_id"]])
        for item in ranked
        if item["post_id"] in anchor_global_positions
    ]

    result = {}
    for item in ranked:
        pid = item["post_id"]
        if pid not in new_post_ids:
            continue
        r = item["rank"]
        rationale = item.get("rationale", "")

        prev = max(
            ((ar, ag) for ar, ag in calibration if ar < r),
            default=None,
            key=lambda x: x[0],
        )
        nxt = min(
            ((ar, ag) for ar, ag in calibration if ar > r),
            default=None,
            key=lambda x: x[0],
        )

        if prev is None and nxt is None:
            frac = float(r)
        elif prev is None:
            frac = float(nxt[1]) - 0.5
        elif nxt is None:
            frac = float(prev[1]) + 0.5
        else:
            prev_rank, prev_global = prev
            next_rank, next_global = nxt
            t = (r - prev_rank) / (next_rank - prev_rank) if next_rank != prev_rank else 0.5
            frac = prev_global + t * (next_global - prev_global)
            frac = max(frac, prev_global + 0.01)
            frac = min(frac, next_global - 0.01)

        result[pid] = (frac, rationale)

    return result


def apply_incremental_positions(
    session,
    new_positions: dict[str, float],
    new_rationales: dict[str, str],
) -> None:
    """
    Merge new posts into the existing global ranking and renumber all ranked posts.
    Queries all currently ranked posts, merges in new ones at fractional positions,
    then re-assigns integer positions 1..N.
    """
    ranked_posts = (
        session.query(JobPost)
        .filter(JobPost.global_rerank_position.isnot(None))
        .filter(JobPost.status.notin_(INACTIVE_STATUSES))
        .order_by(JobPost.global_rerank_position)
        .all()
    )

    merged = [(float(p.global_rerank_position), p.id) for p in ranked_posts]
    for post_id, frac_pos in new_positions.items():
        merged.append((frac_pos, post_id))
    merged.sort(key=lambda x: x[0])

    all_ids = {pid for _, pid in merged}
    posts_by_id = {
        p.id: p
        for p in session.query(JobPost).filter(JobPost.id.in_(all_ids)).all()
    }

    for new_pos, (_, post_id) in enumerate(merged, 1):
        post = posts_by_id.get(post_id)
        if post:
            post.global_rerank_position = new_pos
            if post_id in new_rationales:
                post.rerank_rationale = new_rationales[post_id]


# ── RerankerService ─────────────────────────────────────────────────────────────

@dataclass
class RerankerResult:
    ranked: int = 0
    skipped: bool = False
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.skipped:
            return "Skipped: no new posts with score >= 80 and no global rank yet"
        return f"Ranked: {self.ranked} new posts | Errors: {len(self.errors)}"


class RerankerService:
    def __init__(self, model: str = "haiku") -> None:
        self._model = model
        init_db()

    def run(self, dry_run: bool = False) -> RerankerResult:
        result = RerankerResult()

        with SessionLocal() as session:
            new_posts = (
                session.query(JobPost)
                .filter(
                    JobPost.score >= _MIN_SCORE,
                    JobPost.global_rerank_position.is_(None),
                    JobPost.status.notin_(INACTIVE_STATUSES),
                )
                .all()
            )
            if not new_posts:
                result.skipped = True
                return result

            anchor_posts = (
                session.query(JobPost)
                .filter(
                    JobPost.global_rerank_position.isnot(None),
                    JobPost.status.notin_(INACTIVE_STATUSES),
                )
                .order_by(JobPost.global_rerank_position)
                .limit(20)
                .all()
            )
            anchor_global_positions = {p.id: p.global_rerank_position for p in anchor_posts}
            all_posts = new_posts + anchor_posts
            post_dicts = {p.id: _post_to_dict(p) for p in all_posts}

            if len(new_posts) <= 20:
                result = self._run_single_batch(
                    session, new_posts, anchor_posts, anchor_global_positions,
                    post_dicts, dry_run,
                )
            else:
                result = self._run_windowed(
                    session, new_posts, anchor_posts, anchor_global_positions,
                    post_dicts, dry_run,
                )

        return result

    def _run_single_batch(
        self, session, new_posts, anchor_posts, anchor_global_positions,
        post_dicts, dry_run,
    ) -> RerankerResult:
        result = RerankerResult()
        try:
            rankings = rank_with_anchors(
                new_posts, anchor_posts, anchor_global_positions,
                post_dicts, self._model,
            )
            positions_map = assign_incremental_positions(
                rankings, {p.id for p in new_posts}, anchor_global_positions,
            )
            if not dry_run:
                apply_incremental_positions(
                    session,
                    {pid: frac for pid, (frac, _) in positions_map.items()},
                    {pid: rat for pid, (_, rat) in positions_map.items()},
                )
                session.commit()
            result.ranked = len(positions_map)
        except Exception as exc:
            result.errors.append(str(exc))
            logger.error("Incremental rerank failed: %s", exc)
        return result

    def _run_windowed(
        self, session, new_posts, anchor_posts, anchor_global_positions,
        post_dicts, dry_run,
    ) -> RerankerResult:
        """
        Handle >20 new posts: split into windows of 20, run each with top-10 anchors
        in parallel, rank each window's results against all anchors to assign positions.
        """
        result = RerankerResult()
        top_10_anchors = anchor_posts[:10]
        top_10_positions = {p.id: anchor_global_positions[p.id] for p in top_10_anchors}
        windows = [new_posts[i:i + 20] for i in range(0, len(new_posts), 20)]

        def rank_window(window: list[JobPost]) -> tuple[list[JobPost], list[dict]]:
            rankings = rank_with_anchors(
                window, top_10_anchors, top_10_positions, post_dicts, self._model,
            )
            return window, rankings

        all_positions: dict[str, float] = {}
        all_rationales: dict[str, str] = {}

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(rank_window, w): w for w in windows}
            for fut in as_completed(futures):
                window = futures[fut]
                try:
                    window_posts, rankings = fut.result()
                    positions_map = assign_incremental_positions(
                        rankings, {p.id for p in window_posts}, top_10_positions,
                    )
                    all_positions.update({pid: frac for pid, (frac, _) in positions_map.items()})
                    all_rationales.update({pid: rat for pid, (_, rat) in positions_map.items()})
                except Exception as exc:
                    result.errors.append(f"Window failed: {exc}")
                    logger.error("Window rerank failed: %s", exc)

        if all_positions:
            try:
                if not dry_run:
                    apply_incremental_positions(session, all_positions, all_rationales)
                    session.commit()
                result.ranked = len(all_positions)
            except Exception as exc:
                result.errors.append(f"DB write failed: {exc}")
                logger.error("Windowed rerank DB write failed: %s", exc)

        return result


# ── CLI entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Daily incremental reranker")
    parser.add_argument("--model", default="haiku", help="Claude model alias (default: haiku)")
    parser.add_argument("--dry-run", action="store_true", help="Compute positions but don't write to DB")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)
    service = RerankerService(model=args.model)
    print(f"Model : {args.model} | Mode: {'dry-run' if args.dry_run else 'live'}")
    result = service.run(dry_run=args.dry_run)
    print(result)
    if result.errors:
        print("\nErrors:")
        for e in result.errors:
            print(f"  - {e}")
        # Non-zero exit so run_pipeline.sh counts it as a pipeline error
        sys.exit(1)


if __name__ == "__main__":
    main()
