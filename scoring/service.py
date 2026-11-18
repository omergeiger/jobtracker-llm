import argparse
import logging
import math
import time
from dataclasses import dataclass, field

from sqlalchemy import func

from ingestion.db import SessionLocal, init_db, JobPost
from logging_config import setup_logging
from scoring.engines.base import ScoringEngine, ScoreResult

logger = logging.getLogger(__name__)

_COMMIT_BATCH = 10       # commit every N posts
_BATCH_DELAY_S = 2       # seconds to sleep after each commit (reduces sustained CPU load)
_BAR = "─" * 58
_CLAUDE_DEFAULT_MIN_SCORE = 70  # applied when --engine claude is used without --min-score


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _print_batch_summary(
    batch_num: int,
    total_batches: int,
    processed: int,
    total: int,
    batch_errors: int,
    total_errors: int,
    elapsed: float,
) -> None:
    rate = elapsed / processed if processed else 0.0
    remaining_posts = total - processed
    eta = rate * remaining_posts

    print(f"\n── Batch {batch_num} / {total_batches} committed {_BAR[:max(0, 40 - len(str(batch_num)) - len(str(total_batches)))]}")
    print(f"   Processed    : {processed} / {total}")
    print(f"   Errors       : {batch_errors} this batch  |  {total_errors} total")
    print(f"   Elapsed      : {_fmt_duration(elapsed)}  ({rate:.1f}s/post avg)")
    print(f"   Est. remaining : ~{_fmt_duration(eta)}")
    print(_BAR + "\n", flush=True)


@dataclass
class ScoringResult:
    scored: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"Scored: {self.scored} | "
            f"Skipped (already scored): {self.skipped} | "
            f"Errors: {len(self.errors)}"
        )


def _build_engine(engine_name: str, model: str | None = None) -> ScoringEngine:
    if engine_name == "rules":
        from scoring.engines.rules import RulesEngine
        return RulesEngine()

    if engine_name == "ollama":
        from scoring.engines.ollama_engine import OllamaEngine
        return OllamaEngine(model=model) if model else OllamaEngine()

    if engine_name == "hf":
        from scoring.engines.huggingface import HuggingFaceEngine
        return HuggingFaceEngine()

    if engine_name == "claude":
        from scoring.engines.claude_engine import ClaudeEngine
        return ClaudeEngine(model=model) if model else ClaudeEngine()

    # "auto": try Ollama first, fall back to rules
    try:
        from scoring.engines.ollama_engine import OllamaEngine
        engine = OllamaEngine(model=model) if model else OllamaEngine()
        logger.info("Using Ollama engine (%s)", engine.name)
        return engine
    except Exception as exc:
        logger.warning("Ollama unavailable (%s) - falling back to rules engine", exc)
        from scoring.engines.rules import RulesEngine
        return RulesEngine()


def _post_to_dict(post: JobPost) -> dict:
    ef = post.extracted_fields or {}
    return {
        "title": post.title,
        "company_name": ef.get("company_name", ""),
        "location_raw": ef.get("location_raw", ""),
        "remote_type": ef.get("remote_type", "unknown"),
        "office_days": ef.get("office_days"),
        "seniority_level": ef.get("seniority_level", "mid"),
        "department": ef.get("department"),
        "raw_description": post.raw_description or "",
    }


class ScoringService:
    def __init__(self, engine: str = "auto", model: str | None = None) -> None:
        init_db()
        self._engine: ScoringEngine = _build_engine(engine, model=model)

    def score_posts(
        self, rescore: bool = False, upgrade_rules: bool = False,
        batch_delay: float = _BATCH_DELAY_S, min_score: float | None = None,
        statuses: list[str] | None = None,
    ) -> ScoringResult:
        result = ScoringResult()

        with SessionLocal() as session:
            query = session.query(JobPost)
            if upgrade_rules:
                # Target posts scored by the rules engine only
                query = query.filter(
                    func.json_extract(JobPost.score_breakdown, "$.engine") == "rules"
                )
            elif not rescore:
                query = query.filter(JobPost.score.is_(None))
            if statuses:
                query = query.filter(JobPost.status.in_(statuses))
            if min_score is not None:
                # Combine with --rescore/--upgrade-rules to refine only already-promising
                # posts — keeps slow/costly engines (e.g. claude) off the full backlog.
                query = query.filter(JobPost.score >= min_score)
            posts = query.all()

            total = len(posts)
            total_batches = math.ceil(total / _COMMIT_BATCH)
            batch_errors = 0
            start_time = time.perf_counter()

            logger.info("Starting scoring: %d posts, engine=%s", total, self._engine.name)

            for i, post in enumerate(posts, 1):
                try:
                    post_dict = _post_to_dict(post)
                    score_result: ScoreResult = self._engine.score(post_dict)

                    post.score = score_result.score
                    post.score_breakdown = score_result.to_dict()
                    result.scored += 1

                    flag = " DEALBREAKER" if score_result.dealbreaker else ""
                    logger.debug(
                        "[%s] %s / %s -> %.0f%s",
                        score_result.engine,
                        post.title,
                        post_dict["company_name"],
                        score_result.score,
                        flag,
                    )
                except Exception as exc:
                    msg = f"{post.title}: {exc}"
                    result.errors.append(msg)
                    batch_errors += 1
                    logger.error("Scoring failed: %s", msg)

                if i % _COMMIT_BATCH == 0:
                    session.commit()
                    _print_batch_summary(
                        batch_num=i // _COMMIT_BATCH,
                        total_batches=total_batches,
                        processed=i,
                        total=total,
                        batch_errors=batch_errors,
                        total_errors=len(result.errors),
                        elapsed=time.perf_counter() - start_time,
                    )
                    batch_errors = 0
                    if batch_delay > 0:
                        time.sleep(batch_delay)

            session.commit()  # flush remaining posts in the final partial batch

        logger.info("Scoring complete: %s", result)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Score job posts against user profile")
    parser.add_argument(
        "--engine",
        choices=["auto", "hf", "ollama", "rules", "claude"],
        default="auto",
        help="Scoring engine (default: auto — tries Ollama, falls back to rules)",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help="Model override: Ollama model name (e.g. llama3.1:70b; only for --engine "
             "ollama/auto, default: qwen2.5:7b) or Claude model alias (e.g. opus, haiku; "
             "only for --engine claude, default: sonnet)",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Re-score posts that already have a score",
    )
    parser.add_argument(
        "--upgrade-rules",
        action="store_true",
        dest="upgrade_rules",
        help="Re-score only posts previously scored by the rules engine",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        dest="min_score",
        metavar="SCORE",
        help="Only (re)score posts with an existing score >= SCORE. Combine with --rescore or "
             "--upgrade-rules to refine already-promising posts — keeps slow/costly engines "
             f"(e.g. claude) off the full backlog. Default for --engine claude: "
             f"{_CLAUDE_DEFAULT_MIN_SCORE} (pass --min-score explicitly to override).",
    )
    parser.add_argument(
        "--status",
        action="append",
        dest="statuses",
        metavar="STATUS",
        help="Only (re)score posts with this status (repeatable, e.g. --status new). "
             "Default: all statuses, including closed/rejected.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=_BATCH_DELAY_S,
        metavar="SECS",
        help=f"Seconds to sleep after each committed batch (default: {_BATCH_DELAY_S}, 0 to disable)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-post scoring details",
    )
    args = parser.parse_args()

    if args.engine == "claude" and args.min_score is None:
        args.min_score = _CLAUDE_DEFAULT_MIN_SCORE
        print(
            f"No --min-score given for --engine claude; defaulting to "
            f"{_CLAUDE_DEFAULT_MIN_SCORE} (precaution against scoring the full backlog)."
        )

    setup_logging(verbose=args.verbose)

    service = ScoringService(engine=args.engine, model=args.model)
    print(f"Engine : {service._engine.name}")
    if args.upgrade_rules:
        mode = "upgrade rules-scored → current engine"
    elif args.rescore:
        mode = "rescore all"
    else:
        mode = "score unscored only"
    if args.min_score is not None:
        mode += f" (score >= {args.min_score})"
    if args.statuses:
        mode += f" (status in {args.statuses})"
    print(f"Mode   : {mode}")
    print()

    result = service.score_posts(
        rescore=args.rescore,
        upgrade_rules=args.upgrade_rules,
        batch_delay=args.delay,
        min_score=args.min_score,
        statuses=args.statuses,
    )
    print(result)

    if result.errors:
        print("\nErrors:")
        for err in result.errors:
            print(f"  - {err}")


if __name__ == "__main__":
    main()
