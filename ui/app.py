"""
Job Tracker — Streamlit UI.

Run from project root:
    streamlit run ui/app.py
"""

import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta, timezone

import streamlit as st
from bs4 import BeautifulSoup

from ui.queries import (
    get_all_feedback_tags,
    get_companies,
    get_company_counts,
    get_post_detail,
    get_posted_within_counts,
    get_rating_counts,
    get_remote_type_counts,
    get_seniority_counts,
    get_stats,
    get_tag_counts,
    load_posts,
    record_action,
    record_feedback_tags,
    record_rating,
)

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Job Tracker",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* Hide Streamlit's auto-generated multipage nav — replaced by an explicit
   page_link button at the bottom of the sidebar. */
[data-testid="stSidebarNav"] { display: none; }

/* Sidebar should always stay open — no collapse control. */
[data-testid="stSidebarCollapseButton"] { display: none; }
[data-testid="stSidebarCollapsedControl"] { display: none; }

/* 60% wider than Streamlit's 21rem default. */
[data-testid="stSidebar"] { width: 538px !important; min-width: 538px !important; }

/* ── Feed card: HTML visual card + invisible button overlay ─── */

.feed-card {
    border: 1px solid #e5e7eb;
    border-left: 4px solid #e5e7eb;
    border-radius: 8px;
    padding: 10px 14px 10px;
    background: #ffffff;
    pointer-events: none;   /* clicks pass through to button below */
    height: 92px;
    box-sizing: border-box;
    overflow: hidden;
}
.feed-card.selected {
    border-color: #3b82f6;
    border-left-color: #3b82f6;
    background: #eff6ff;
}

/* Collapse the button wrapper so it takes no layout space */
.stElementContainer:has(.feed-card) + .stElementContainer,
.element-container:has(.feed-card) + .element-container {
    height: 0 !important;
    overflow: visible !important;
    margin: 0 !important;
    padding: 0 !important;
    position: relative !important;
}

/* Invisible button positioned absolutely over the card above */
.stElementContainer:has(.feed-card) + .stElementContainer button,
.element-container:has(.feed-card) + .element-container button {
    position: absolute !important;
    top: -112px !important;   /* card height 92px + ~20px gap */
    left: 0 !important;
    width: 100% !important;
    height: 112px !important;
    opacity: 0.001 !important;
    border: none !important;
    background: transparent !important;
    cursor: pointer !important;
    z-index: 10 !important;
}
</style>
""", unsafe_allow_html=True)

# ── Color maps ─────────────────────────────────────────────────────────────────

STATUS_COLORS = {
    "new":      "#3b82f6",
    "reviewed": "#f59e0b",
    "applied":  "#22c55e",
    "rejected": "#ef4444",
    "archived": "#6b7280",
    "closed":   "#a855f7",
}

REMOTE_COLORS = {
    "remote":  "#16a34a",
    "hybrid":  "#2563eb",
    "onsite":  "#ea580c",
    "unknown": "#9ca3af",
}

SENIORITY_COLORS = {
    "director":  "#7c3aed",
    "vp":        "#7c3aed",
    "principal": "#8b5cf6",
    "staff":     "#6366f1",
    "lead":      "#818cf8",
    "senior":    "#3b82f6",
    "manager":   "#0891b2",
    "mid":       "#9ca3af",
    "junior":    "#6b7280",
}

TAG_GRAY = "#6b7280"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _badge(label: str, color: str) -> str:
    return (
        f'<span style="background:{color};color:#fff;padding:2px 9px;'
        f'border-radius:10px;font-size:11px;font-weight:600;'
        f'white-space:nowrap;display:inline-block">{label}</span>'
    )


def _days_ago(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (now - dt).days
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days}d ago"


_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


@st.cache_data(max_entries=500)
def _description_to_text(text: str) -> str:
    """Plain text for the detail view.

    Greenhouse returns HTML-escaped content (`&lt;p&gt;`), so a single parse leaves literal
    tags in the stored text. Unescape and strip again until no tags remain (at most twice).
    """
    for _ in range(2):
        if not (_TAG_RE.search(text) or "&lt;" in text):
            break
        text = BeautifulSoup(html.unescape(text), "html.parser").get_text(separator="\n")
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# ── Session state ──────────────────────────────────────────────────────────────

if "selected_id" not in st.session_state:
    st.session_state.selected_id = None
    st.session_state._initial_load = True
    st.session_state.page = 0
    st.session_state._filter_key = None

# ── Sidebar — filters ──────────────────────────────────────────────────────────


def _filter_row(label: str):
    """Label + control on one line: narrow label column, wider control column."""
    col_label, col_control = st.columns([1, 2], vertical_alignment="center")
    col_label.markdown(
        f'<div style="font-size:13px;font-weight:600;color:#374151">{label}</div>',
        unsafe_allow_html=True,
    )
    return col_control


def _by_count_desc(options: list[str], counts: dict) -> list[str]:
    return sorted(options, key=lambda v: -counts.get(v, 0))


with st.sidebar:
    st.markdown("### 🎯 Job Tracker")

    _status_counts = get_stats()
    _remote_counts = get_remote_type_counts()
    _seniority_counts = get_seniority_counts()
    _company_counts = get_company_counts()
    _posted_counts = get_posted_within_counts()
    _rating_counts = get_rating_counts()
    _all_tags = get_all_feedback_tags()
    _tag_counts = get_tag_counts()

    search = _filter_row("Search").text_input(
        "Search", placeholder="title or company…", label_visibility="collapsed"
    )

    selected_statuses = _filter_row("Status").multiselect(
        "Status",
        _by_count_desc(["new", "reviewed", "applied", "rejected", "closed"], _status_counts),
        default=["new", "reviewed"],
        format_func=lambda v: f"{v} ({_status_counts.get(v, 0)})",
        label_visibility="collapsed",
    )

    posted_within = _filter_row("Posted").selectbox(
        "Posted within",
        _by_count_desc(["Any time", "Last 7 days", "Last 14 days", "Last 30 days"], _posted_counts),
        index=0,
        format_func=lambda v: f"{v} ({_posted_counts.get(v, 0)})",
        label_visibility="collapsed",
    )

    selected_companies = _filter_row("Company").multiselect(
        "Company",
        _by_count_desc(get_companies(), _company_counts),
        default=[],
        format_func=lambda v: f"{v} ({_company_counts.get(v, 0)})",
        label_visibility="collapsed",
    )

    selected_seniority = _filter_row("Seniority").multiselect(
        "Seniority",
        _by_count_desc(
            ["director", "vp", "principal", "staff", "lead", "senior", "manager", "mid", "junior"],
            _seniority_counts,
        ),
        default=[],
        format_func=lambda v: f"{v} ({_seniority_counts.get(v, 0)})",
        label_visibility="collapsed",
    )

    selected_remote = _filter_row("Arrangement").multiselect(
        "Work arrangement",
        _by_count_desc(["remote", "hybrid", "onsite", "unknown"], _remote_counts),
        default=[],
        format_func=lambda v: f"{v} ({_remote_counts.get(v, 0)})",
        label_visibility="collapsed",
    )

    selected_ratings = _filter_row("Rating").multiselect(
        "Rating",
        [5, 4, 3, 2, 1],
        default=[],
        format_func=lambda v: f"{'★' * v} ({_rating_counts.get(v, 0)})",
        label_visibility="collapsed",
    )

    selected_feedback_tags = _filter_row("Feedback").multiselect(
        "Feedback",
        _by_count_desc(_all_tags, _tag_counts),
        default=[],
        format_func=lambda v: f"{v} ({_tag_counts.get(v, 0)})",
        label_visibility="collapsed",
    )

    st.divider()

    _stats = get_stats()
    st.markdown(
        f'<div style="font-size:12px;color:#6b7280;line-height:1.6">'
        f'<b>{_stats.get("total", 0)}</b> total &nbsp;·&nbsp; '
        f'{_stats.get("new", 0)} new &nbsp;·&nbsp; '
        f'{_stats.get("reviewed", 0)} reviewed &nbsp;·&nbsp; '
        f'{_stats.get("applied", 0)} applied &nbsp;·&nbsp; '
        f'{_stats.get("rejected", 0)} rejected &nbsp;·&nbsp; '
        f'{_stats.get("closed", 0)} closed'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.page_link("pages/coverage.py", label="Source Coverage", icon="📊", use_container_width=True)

# ── Load data ──────────────────────────────────────────────────────────────────

all_posts = load_posts(
    statuses=selected_statuses or None,
    remote_types=selected_remote or None,
    seniorities=selected_seniority or None,
    companies=selected_companies or None,
    ratings=selected_ratings or None,
    tags=selected_feedback_tags or None,
    search=search or None,
)

# Post-cache filter: date (applied in-memory so it doesn't bust the cache)
_DAYS_MAP = {"Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30}
days_limit = _DAYS_MAP.get(posted_within)

if days_limit:
    _cutoff = datetime.now(timezone.utc) - timedelta(days=days_limit)
    def _after_cutoff(dt):
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= _cutoff
    all_posts = [p for p in all_posts if _after_cutoff(p["posting_date"])]

# Reset page when filters change
PAGE_SIZE = 50
filter_key = (
    tuple(sorted(selected_statuses)),
    tuple(sorted(selected_remote)),
    tuple(sorted(selected_seniority)),
    tuple(sorted(selected_companies)),
    tuple(sorted(selected_ratings)),
    tuple(sorted(selected_feedback_tags)),
    search or "",
    posted_within,
)
if filter_key != st.session_state._filter_key:
    st.session_state.page = 0
    st.session_state._filter_key = filter_key

page = st.session_state.page
total = len(all_posts)
page_start = page * PAGE_SIZE
page_end   = min(page_start + PAGE_SIZE, total)
posts      = all_posts[page_start:page_end]

# Auto-select highest-ranked post on first load
if st.session_state._initial_load and posts:
    st.session_state.selected_id = posts[0]["id"]
    st.session_state._initial_load = False

# ── Layout: full-width feed when nothing selected, split when detail is open ───

if st.session_state.selected_id:
    feed_col, detail_col = st.columns([2, 3], gap="large")
else:
    feed_col = st.container()

# ── Feed ─────────────────────────────────────────────────────────────────────

with feed_col:
    cap_col, prev_col, next_col = st.columns([4, 1, 1], vertical_alignment="center")
    cap_col.caption(f"{page_start + 1}–{page_end} of {total} posts" if total else "0 posts match the current filters")
    with prev_col:
        if st.button("←", disabled=(page == 0), use_container_width=True):
            st.session_state.page -= 1
            st.session_state.selected_id = all_posts[(st.session_state.page * PAGE_SIZE)]["id"] if all_posts else None
            st.rerun()
    with next_col:
        if st.button("→", disabled=(page_end >= total), use_container_width=True):
            st.session_state.page += 1
            st.session_state.selected_id = all_posts[(st.session_state.page * PAGE_SIZE)]["id"] if all_posts else None
            st.rerun()

    with st.container(height=560):
        if not posts:
            st.info("No posts match the current filters.")

        for post in posts:
            is_selected = st.session_state.selected_id == post["id"]
            card_cls    = "feed-card selected" if is_selected else "feed-card"

            score_html   = (
                f'<span style="font-size:12px;font-weight:700;color:#1d4ed8">⭐ {post["score"]:.0f}</span>'
                if post["score"] is not None
                else '<span style="font-size:11px;color:#9ca3af">—</span>'
            )
            remote_badge = _badge(post["remote_type"],      REMOTE_COLORS.get(post["remote_type"],      "#9ca3af"))
            senior_badge = _badge(post["seniority_level"],  SENIORITY_COLORS.get(post["seniority_level"],"#9ca3af"))
            status_badge = _badge(post["status"],           STATUS_COLORS.get(post["status"],            "#9ca3af"))
            rating_badge = _badge("★" * post["user_rating"], "#d97706") if post.get("user_rating") else ""
            rank = post.get("global_rerank_position")
            rank_prefix = f'<span style="font-size:11px;color:#6b7280;font-weight:600">#{rank} &nbsp;</span>' if rank else ""

            st.markdown(
                f'<div class="{card_cls}">'
                f'<div style="font-weight:700;font-size:13px;color:#111827;margin-bottom:2px">{rank_prefix}{post["company"]}</div>'
                f'<div style="font-size:12px;color:#374151;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{post["title"]}</div>'
                f'<div style="display:flex;gap:5px;flex-wrap:nowrap;align-items:center;overflow:hidden">'
                f'{remote_badge}{senior_badge}{status_badge}{rating_badge}'
                f'<span style="margin-left:auto;font-size:11px;color:#9ca3af;white-space:nowrap">{_days_ago(post["posting_date"])}</span>'
                f'{score_html}</div></div>',
                unsafe_allow_html=True,
            )

            if st.button(" ", key=f"view_{post['id']}", use_container_width=True):
                st.session_state.selected_id = None if is_selected else post["id"]
                st.rerun()

# ── Detail panel (only rendered when a post is selected) ──────────────────────

if st.session_state.selected_id:
    with detail_col:
        detail = get_post_detail(st.session_state.selected_id)

        if not detail:
            st.warning("Post no longer available.")
            st.session_state.selected_id = None
        else:
            # ── Header ─────────────────────────────────────────────────────────
            h_left, h_right = st.columns([5, 1])
            with h_left:
                st.markdown(
                    f'<div style="font-size:13px;color:#6b7280;font-weight:600;'
                    f'text-transform:uppercase;letter-spacing:.05em">{detail["company"]}</div>',
                    unsafe_allow_html=True,
                )
            with h_right:
                if st.button("✕ Close", key="close_detail", use_container_width=True):
                    st.session_state.selected_id = None
                    st.rerun()

            # ── Everything below scrolls within this one module — never the page ─
            with st.container(height=560):
                st.markdown(f"### {detail['title']}")

                # ── Tags row ───────────────────────────────────────────────────
                tags = [_badge(detail["status"], STATUS_COLORS.get(detail["status"], "#6b7280"))]
                tags.append(
                    _badge(f"⭐ {detail['score']:.0f}", "#1d4ed8")
                    if detail["score"] is not None
                    else _badge("not scored", "#9ca3af")
                )
                if detail.get("user_rating"):
                    tags.append(_badge("★" * detail["user_rating"], "#d97706"))
                tags.append(_badge(detail["remote_type"] or "unknown", REMOTE_COLORS.get(detail["remote_type"], "#9ca3af")))
                if detail["seniority_level"]:
                    tags.append(_badge(detail["seniority_level"], SENIORITY_COLORS.get(detail["seniority_level"], "#9ca3af")))
                if detail["office_days"]:
                    tags.append(_badge(f"🏢 {detail['office_days']}", TAG_GRAY))
                if detail["location_raw"]:
                    tags.append(_badge(f"📍 {detail['location_raw']}", TAG_GRAY))
                if detail["department"]:
                    tags.append(_badge(f"🗂 {detail['department']}", TAG_GRAY))
                for feedback_tag in detail["feedback_tags"]:
                    tags.append(_badge(feedback_tag, TAG_GRAY))
                tags.append(_badge(f"📅 {_days_ago(detail['posting_date'])}", TAG_GRAY))

                st.markdown(
                    f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 12px">{"".join(tags)}</div>',
                    unsafe_allow_html=True,
                )

                if detail.get("rerank_rationale"):
                    rank_label = f"Rank #{detail['global_rerank_position']} · " if detail.get("global_rerank_position") else ""
                    st.caption(f"{rank_label}{detail['rerank_rationale']}")

                if detail["apply_url"]:
                    st.link_button("Apply →", detail["apply_url"], type="primary")

                # ── Rating ───────────────────────────────────────────────────────
                rating_key = f"rating_{detail['id']}"
                if rating_key not in st.session_state:
                    st.session_state[rating_key] = (
                        detail["user_rating"] - 1 if detail["user_rating"] else None
                    )
                selected_stars = st.feedback("stars", key=rating_key)
                current_rating = selected_stars + 1 if selected_stars is not None else None
                if current_rating != detail["user_rating"]:
                    record_rating(detail["id"], current_rating)
                    st.rerun()

                # ── Actions ──────────────────────────────────────────────────────
                a1, a2, a3 = st.columns(3)

                with a1:
                    if st.button(
                        "✓ Reviewed",
                        use_container_width=True,
                        disabled=detail["status"] == "reviewed",
                    ):
                        record_action(detail["id"], "saved", new_status="reviewed")
                        st.rerun()

                with a2:
                    if st.button(
                        "✉ Applied",
                        use_container_width=True,
                        disabled=detail["status"] == "applied",
                    ):
                        record_action(detail["id"], "applied", new_status="applied")
                        st.rerun()

                with a3:
                    if st.button(
                        "✗ Dismiss",
                        use_container_width=True,
                        disabled=detail["status"] == "rejected",
                    ):
                        record_action(detail["id"], "dismissed", new_status="rejected")
                        st.rerun()

                selected_feedback = st.multiselect(
                    "Feedback",
                    options=get_all_feedback_tags(),
                    default=detail["feedback_tags"],
                    accept_new_options=True,
                    placeholder="irrelevant domain, bad location, too corporate…",
                    key=f"feedback_tags_{detail['id']}",
                    label_visibility="collapsed",
                )
                if selected_feedback != detail["feedback_tags"]:
                    record_feedback_tags(detail["id"], selected_feedback)
                    st.rerun()

                st.divider()

                st.markdown("**Job Description**")
                desc = _description_to_text(detail["description_text"] or detail["raw_description"] or "")
                if desc:
                    st.text(desc)
                else:
                    st.caption("No description available.")
