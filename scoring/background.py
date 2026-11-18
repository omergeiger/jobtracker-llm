import logging
import os
import subprocess
import threading
from typing import Sequence

logger = logging.getLogger(__name__)


def _post_to_dict(post) -> dict:
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


def apply_rules_sync(post_ids: Sequence[int]) -> None:
    from ingestion.db import SessionLocal, JobPost
    from scoring.engines.rules import RulesEngine

    engine = RulesEngine()
    scored = errors = 0

    logger.info("Rules sync: scoring %d posts", len(post_ids))
    with SessionLocal() as session:
        posts = session.query(JobPost).filter(JobPost.id.in_(list(post_ids))).all()
        for post in posts:
            try:
                result = engine.score(_post_to_dict(post))
                post.score = result.score
                post.score_breakdown = result.to_dict()
                scored += 1
            except Exception as exc:
                errors += 1
                logger.error("Rules scoring failed for %s: %s", post.title, exc)
        session.commit()

    logger.info("Rules scoring applied. Scored: %d | Errors: %d", scored, errors)


def _hf_score_posts(post_ids: Sequence[int]) -> None:
    from ingestion.db import SessionLocal, JobPost
    from scoring.engines.huggingface import HuggingFaceEngine

    engine = HuggingFaceEngine()
    scored = errors = 0

    with SessionLocal() as session:
        posts = session.query(JobPost).filter(JobPost.id.in_(list(post_ids))).all()
        for post in posts:
            try:
                result = engine.score(_post_to_dict(post))
                post.score = result.score
                post.score_breakdown = result.to_dict()
                session.commit()
                scored += 1
                logger.info(
                    "[hf-bg] %s / %s -> %.0f%s",
                    post.title,
                    _post_to_dict(post)["company_name"],
                    result.score,
                    " [DEALBREAKER]" if result.dealbreaker else "",
                )
            except Exception as exc:
                errors += 1
                session.rollback()
                logger.error("[hf-bg] Failed: %s - %s", post.title, exc)

    logger.info("[hf-bg] Complete. Scored: %d | Errors: %d", scored, errors)


def launch_hf_background(post_ids: Sequence[int]) -> threading.Thread | None:
    if not post_ids:
        return None

    try:
        subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[hf-bg] caffeinate active - Mac will stay awake until scoring completes")
    except FileNotFoundError:
        logger.warning("[hf-bg] caffeinate not found - system may sleep before HF scoring completes")

    t = threading.Thread(
        target=_hf_score_posts,
        args=(post_ids,),
        daemon=False,
        name="hf-scoring",
    )
    t.start()
    logger.info("[hf-bg] HF background scoring started for %d posts", len(post_ids))
    return t


def _ollama_score_posts(post_ids: Sequence[int]) -> None:
    from ingestion.db import SessionLocal, JobPost
    from scoring.engines.ollama_engine import OllamaEngine

    try:
        engine = OllamaEngine()
    except Exception as exc:
        logger.warning("[ollama-bg] OllamaEngine init failed (Ollama may be offline): %s", exc)
        return

    scored = errors = 0
    with SessionLocal() as session:
        posts = session.query(JobPost).filter(JobPost.id.in_(list(post_ids))).all()
        for post in posts:
            try:
                result = engine.score(_post_to_dict(post))
                post.score = result.score
                post.score_breakdown = result.to_dict()
                session.commit()
                scored += 1
                logger.info(
                    "[ollama-bg] %s / %s -> %.0f%s",
                    post.title,
                    _post_to_dict(post)["company_name"],
                    result.score,
                    " [DEALBREAKER]" if result.dealbreaker else "",
                )
            except Exception as exc:
                errors += 1
                session.rollback()
                logger.error("[ollama-bg] Failed: %s - %s", post.title, exc)

    logger.info("[ollama-bg] Complete. Scored: %d | Errors: %d", scored, errors)


def launch_ollama_background(post_ids: Sequence[int]) -> threading.Thread | None:
    if not post_ids:
        return None

    try:
        subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[ollama-bg] caffeinate active - Mac will stay awake until scoring completes")
    except FileNotFoundError:
        logger.warning("[ollama-bg] caffeinate not found - system may sleep before Ollama scoring completes")

    t = threading.Thread(
        target=_ollama_score_posts,
        args=(post_ids,),
        daemon=False,
        name="ollama-scoring",
    )
    t.start()
    logger.info("[ollama-bg] Ollama background scoring started for %d posts", len(post_ids))
    return t


def score_after_ingest(post_ids: Sequence[int]) -> None:
    """Entry point called by IngestService. Rules sync first, then Ollama in background."""
    if not post_ids:
        return
    apply_rules_sync(post_ids)
    launch_ollama_background(post_ids)
