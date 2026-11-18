"""
Shared read queries used by both the Streamlit UI (ui/queries.py) and the
email report (scripts/daily_report.py). Living here means both surfaces
filter and rank posts identically instead of each maintaining its own
copy of the "active post" query that can silently drift apart.
"""

from sqlalchemy.orm import Session

from .models import JobPost

# Posts the user has dismissed or that are confirmed dead — excluded from
# every "active feed" view (UI default feed, ranked view, email report).
INACTIVE_STATUSES = ("rejected", "closed", "archived")


def get_active_top_ranked_posts(session: Session, limit: int | None = None) -> list[JobPost]:
    """Posts ordered by global_rerank_position ascending, excluding dismissed/dead posts."""
    query = (
        session.query(JobPost)
        .filter(JobPost.global_rerank_position.isnot(None))
        .filter(JobPost.status.notin_(INACTIVE_STATUSES))
        .order_by(JobPost.global_rerank_position.asc())
    )
    if limit:
        query = query.limit(limit)
    return query.all()
