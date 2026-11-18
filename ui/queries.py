"""
DB query helpers for the Streamlit UI.
All reads go through SQLAlchemy sessions; writes are wrapped in explicit commits.
"""

from datetime import datetime, timedelta, timezone

import streamlit as st
from sqlalchemy import bindparam, func, text

from ingestion.db import SessionLocal, JobPost, Company, UserAction, INACTIVE_STATUSES, get_active_top_ranked_posts

_NEW_DAYS = 7  # badge threshold for top-picks views


@st.cache_data(ttl=300)
def load_posts(
    statuses: list[str] | None = None,
    remote_types: list[str] | None = None,
    seniorities: list[str] | None = None,
    companies: list[str] | None = None,
    ratings: list[int] | None = None,
    tags: list[str] | None = None,
    search: str | None = None,
) -> list[dict]:
    """Return feed rows sorted by score desc (nulls last), then posting_date desc."""
    with SessionLocal() as session:
        query = (
            session.query(JobPost, Company)
            .join(Company, JobPost.company_id == Company.id)
        )

        if statuses:
            query = query.filter(JobPost.status.in_(statuses))
        if companies:
            query = query.filter(Company.name.in_(companies))
        if ratings:
            query = query.filter(JobPost.user_rating.in_(ratings))
        if search:
            term = f"%{search}%"
            query = query.filter(
                JobPost.title.ilike(term) | Company.name.ilike(term)
            )

        # ranked posts first (nulls last), then by score desc
        query = query.order_by(
            func.coalesce(JobPost.global_rerank_position, 99999).asc(),
            func.coalesce(JobPost.score, -999).desc(),
            JobPost.posting_date.desc(),
        )

        rows = query.all()

    result = []
    for post, company in rows:
        ef = post.extracted_fields or {}

        if remote_types and ef.get("remote_type") not in remote_types:
            continue
        if seniorities and ef.get("seniority_level") not in seniorities:
            continue
        if tags and not (set(post.feedback_tags or []) & set(tags)):
            continue

        result.append({
            "id": post.id,
            "title": post.title,
            "company": company.name,
            "apply_url": post.apply_url,
            "posting_date": post.posting_date,
            "status": post.status,
            "score": post.score,
            "user_rating": post.user_rating,
            "feedback_tags": post.feedback_tags or [],
            "global_rerank_position": post.global_rerank_position,
            "remote_type": ef.get("remote_type", "unknown"),
            "office_days": ef.get("office_days"),
            "seniority_level": ef.get("seniority_level", "mid"),
            "department": ef.get("department"),
            "location_raw": ef.get("location_raw", ""),
        })

    return result


@st.cache_data(ttl=300)
def get_post_detail(post_id: str) -> dict | None:
    with SessionLocal() as session:
        row = (
            session.query(JobPost, Company)
            .join(Company)
            .filter(JobPost.id == post_id)
            .first()
        )
        if not row:
            return None
        post, company = row
        ef = post.extracted_fields or {}
        return {
            "id": post.id,
            "title": post.title,
            "company": company.name,
            "apply_url": post.apply_url,
            "posting_date": post.posting_date,
            "ingested_at": post.ingested_at,
            "status": post.status,
            "score": post.score,
            "user_rating": post.user_rating,
            "feedback_tags": post.feedback_tags or [],
            "score_breakdown": post.score_breakdown,
            "raw_description": post.raw_description,
            "rerank_rationale": post.rerank_rationale or "",
            "global_rerank_position": post.global_rerank_position,
            "description_text": ef.get("description_text") or "",
            "remote_type": ef.get("remote_type", "unknown"),
            "office_days": ef.get("office_days"),
            "seniority_level": ef.get("seniority_level"),
            "department": ef.get("department"),
            "location_raw": ef.get("location_raw", ""),
        }


def record_action(
    post_id: str,
    action: str,
    note: str | None = None,
    new_status: str | None = None,
) -> None:
    with SessionLocal() as session:
        session.add(UserAction(post_id=post_id, action=action, note=note))
        if new_status:
            post = session.query(JobPost).filter_by(id=post_id).first()
            if post:
                post.status = new_status
                if new_status in INACTIVE_STATUSES:
                    post.global_rerank_position = None
                    post.rerank_rationale = None
        session.commit()
    st.cache_data.clear()


def record_rating(post_id: str, rating: int | None) -> None:
    """Persist (or clear, if rating is None) a 1-5 user rating.

    Advances status new -> reviewed when setting a rating; leaves other statuses
    alone, and clearing a rating never changes status.
    """
    with SessionLocal() as session:
        post = session.query(JobPost).filter_by(id=post_id).first()
        if post:
            post.user_rating = rating
            if rating is not None and post.status == "new":
                post.status = "reviewed"
        session.add(UserAction(
            post_id=post_id,
            action="rated" if rating is not None else "unrated",
            note=str(rating) if rating is not None else None,
        ))
        session.commit()
    st.cache_data.clear()


def record_feedback_tags(post_id: str, tags: list[str]) -> None:
    """Persist the full set of reusable feedback tags for a post.

    Advances status new -> reviewed when tags is non-empty; leaves other statuses
    alone, and clearing all tags never changes status (same ratchet rule as rating).
    """
    with SessionLocal() as session:
        post = session.query(JobPost).filter_by(id=post_id).first()
        if post:
            post.feedback_tags = tags
            if tags and post.status == "new":
                post.status = "reviewed"
        session.add(UserAction(
            post_id=post_id,
            action="tagged" if tags else "untagged",
            note=", ".join(tags) if tags else None,
        ))
        session.commit()
    st.cache_data.clear()


def update_status(post_id: str, new_status: str) -> None:
    with SessionLocal() as session:
        post = session.query(JobPost).filter_by(id=post_id).first()
        if post:
            post.status = new_status
            session.commit()


@st.cache_data(ttl=300)
def get_companies() -> list[str]:
    with SessionLocal() as session:
        return [
            name for (name,) in
            session.query(Company.name).order_by(Company.name).all()
        ]


@st.cache_data(ttl=300)
def get_company_counts() -> dict:
    """Counts exclude closed/rejected/archived posts — those aren't part of the active feed."""
    with SessionLocal() as session:
        rows = (
            session.query(Company.name, func.count(JobPost.id))
            .join(JobPost, JobPost.company_id == Company.id)
            .filter(JobPost.status.notin_(INACTIVE_STATUSES))
            .group_by(Company.name)
            .all()
        )
    return dict(rows)


@st.cache_data(ttl=300)
def get_remote_type_counts() -> dict:
    """Counts exclude closed/rejected/archived posts — those aren't part of the active feed."""
    with SessionLocal() as session:
        rows = session.execute(
            text("""
                SELECT COALESCE(json_extract(extracted_fields, '$.remote_type'), 'unknown') AS v, COUNT(*)
                FROM job_posts WHERE status NOT IN :statuses GROUP BY v
            """).bindparams(bindparam("statuses", expanding=True)),
            {"statuses": list(INACTIVE_STATUSES)},
        ).fetchall()
    return dict(rows)


@st.cache_data(ttl=300)
def get_seniority_counts() -> dict:
    """Counts exclude closed/rejected/archived posts — those aren't part of the active feed."""
    with SessionLocal() as session:
        rows = session.execute(
            text("""
                SELECT COALESCE(json_extract(extracted_fields, '$.seniority_level'), 'mid') AS v, COUNT(*)
                FROM job_posts WHERE status NOT IN :statuses GROUP BY v
            """).bindparams(bindparam("statuses", expanding=True)),
            {"statuses": list(INACTIVE_STATUSES)},
        ).fetchall()
    return dict(rows)


@st.cache_data(ttl=300)
def get_rating_counts() -> dict:
    """Counts exclude closed/rejected/archived posts — those aren't part of the active feed."""
    with SessionLocal() as session:
        rows = (
            session.query(JobPost.user_rating, func.count(JobPost.id))
            .filter(
                JobPost.status.notin_(INACTIVE_STATUSES),
                JobPost.user_rating.isnot(None),
            )
            .group_by(JobPost.user_rating)
            .all()
        )
    return dict(rows)


@st.cache_data(ttl=300)
def get_all_feedback_tags() -> list[str]:
    """Distinct feedback tags across all posts, most-used first — the reusable vocabulary."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT je.value AS tag, COUNT(*) AS uses
            FROM job_posts, json_each(job_posts.feedback_tags) je
            GROUP BY je.value
            ORDER BY uses DESC
        """)).fetchall()
    return [tag for tag, _ in rows]


@st.cache_data(ttl=300)
def get_tag_counts() -> dict:
    """Counts exclude closed/rejected/archived posts — those aren't part of the active feed."""
    with SessionLocal() as session:
        rows = session.execute(
            text("""
                SELECT je.value AS tag, COUNT(*) AS uses
                FROM job_posts, json_each(job_posts.feedback_tags) je
                WHERE job_posts.status NOT IN :statuses
                GROUP BY je.value
            """).bindparams(bindparam("statuses", expanding=True)),
            {"statuses": list(INACTIVE_STATUSES)},
        ).fetchall()
    return dict(rows)


@st.cache_data(ttl=300)
def get_posted_within_counts() -> dict:
    """Counts exclude closed/rejected posts — those aren't part of the active feed."""
    windows = {"Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30}
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        base = session.query(func.count(JobPost.id)).filter(
            JobPost.status.notin_(INACTIVE_STATUSES)
        )
        counts = {"Any time": base.scalar()}
        for label, days in windows.items():
            cutoff = now - timedelta(days=days)
            counts[label] = base.filter(JobPost.posting_date >= cutoff).scalar()
    return counts


@st.cache_data(ttl=300)
def get_coverage_stats() -> list[dict]:
    """Return per-company job counts and last ingest time, sorted by job count desc."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT
                json_extract(j.extracted_fields, '$.company_name') AS company,
                s.name AS source,
                COUNT(*) AS jobs,
                MAX(j.ingested_at) AS last_ingested
            FROM job_posts j
            JOIN sources s ON j.source_id = s.id
            GROUP BY company, s.name
            ORDER BY jobs DESC
        """)).fetchall()
    return [
        {
            "Company": row[0] or "(unknown)",
            "Source": row[1],
            "Jobs": row[2],
            "Last ingested": row[3],
        }
        for row in rows
    ]


@st.cache_data(ttl=300)
def get_top10_global() -> list[dict]:
    """Top 10 posts by global_rerank_position, excluding dismissed/dead posts."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_NEW_DAYS)
    with SessionLocal() as session:
        posts = get_active_top_ranked_posts(session, limit=10)
        result = []
        for post in posts:
            ef = post.extracted_fields or {}
            ingested = post.ingested_at
            if ingested and ingested.tzinfo is None:
                ingested = ingested.replace(tzinfo=timezone.utc)
            company_name = post.company.name if post.company else None
            result.append({
                "id": post.id,
                "title": post.title,
                "company": company_name,
                "apply_url": post.apply_url,
                "posting_date": post.posting_date,
                "status": post.status,
                "score": post.score,
                "global_rerank_position": post.global_rerank_position,
                "rerank_rationale": post.rerank_rationale or "",
                "is_new": ingested is not None and ingested >= cutoff,
                "remote_type": ef.get("remote_type", "unknown"),
                "seniority_level": ef.get("seniority_level", "mid"),
                "location_raw": ef.get("location_raw", ""),
            })
    return result


@st.cache_data(ttl=300)
def get_stats() -> dict:
    with SessionLocal() as session:
        rows = (
            session.query(JobPost.status, func.count(JobPost.id))
            .group_by(JobPost.status)
            .all()
        )
    counts = dict(rows)
    counts["total"] = sum(counts.values())
    return counts
