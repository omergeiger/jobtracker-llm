#!/usr/bin/env python3
"""
Daily job report: query new posts from the last N days, email top results.

Usage:
    python scripts/daily_report.py
    python scripts/daily_report.py --days 14 --top 25
    python scripts/daily_report.py --hours 48 --top 25   # override with hours
    python scripts/daily_report.py --dry-run   # print HTML to stdout, no email sent

Required env vars (in .env):
    GMAIL_USER          sender address (e.g. you@gmail.com)
    GMAIL_APP_PASSWORD  Gmail App Password (not your login password)
    REPORT_RECIPIENT    recipient address (defaults to GMAIL_USER if unset)
"""

import argparse
import html
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from ingestion.db import SessionLocal, init_db, JobPost, Source, INACTIVE_STATUSES, get_active_top_ranked_posts
from scripts.run_status import parse_run_log, render_html as render_status_html, subject_marker

_RERANK_MIN_SCORE = 80

_DEFAULT_DAYS = 7
_DEFAULT_TOP = 20
_DEFAULT_DB_TOP = 10
_SCORE_TIERS = [(80, "#1a7f37"), (60, "#0969da"), (40, "#9a6700"), (0, "#6e6e6e")]

_SOURCE_LABELS = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    "comeet": "Comeet",
    "jobspy": "LinkedIn",
}


def _score_color(score) -> str:
    if score is None:
        return "#6e6e6e"
    for threshold, color in _SCORE_TIERS:
        if score >= threshold:
            return color
    return "#6e6e6e"


def _tier_label(score) -> str:
    if score is None:
        return "unscored"
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Strong"
    if score >= 40:
        return "Moderate"
    return "Weak"


def _h(text) -> str:
    return html.escape(str(text or ""))


def _fmt_date(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%-d %b")


def _detach_and_map_sources(session, posts: list[JobPost]) -> dict[str, str]:
    """Build source_id→label map and load attributes needed after session closes."""
    source_ids = {p.source_id for p in posts if p.source_id}
    sources = session.query(Source).filter(Source.id.in_(source_ids)).all()
    source_map = {
        s.id: _SOURCE_LABELS.get(s.name, s.name.replace("_", " ").title())
        for s in sources
    }
    for p in posts:
        _ = p.score_breakdown
        _ = p.extracted_fields
        _ = p.ingested_at
        _ = p.posting_date
        _ = p.global_rerank_position
        _ = p.rerank_rationale
    return source_map


def fetch_new_posts(days: int) -> tuple[list[JobPost], dict[str, str]]:
    """Returns (posts sorted by score desc, source_id→label map), excluding dismissed/dead posts.

    Uses the same INACTIVE_STATUSES exclusion as the UI's active feed, so the email never
    surfaces posts already rejected/closed/archived in the viewer.
    """
    init_db()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as session:
        posts = (
            session.query(JobPost)
            .filter(JobPost.ingested_at >= cutoff)
            .filter(JobPost.status.notin_(INACTIVE_STATUSES))
            .order_by(JobPost.score.desc().nulls_last())
            .all()
        )
        source_map = _detach_and_map_sources(session, posts)
        return posts, source_map


def fetch_top_db_posts(limit: int) -> tuple[list[JobPost], dict[str, str]]:
    """Top posts by global rerank position across the whole DB.

    Shares get_active_top_ranked_posts() with the UI's get_top10_global() so both
    surfaces apply the identical status exclusion instead of maintaining it twice.
    """
    init_db()
    with SessionLocal() as session:
        posts = get_active_top_ranked_posts(session, limit=limit)
        source_map = _detach_and_map_sources(session, posts)
        return posts, source_map


def fetch_pipeline_counts() -> dict:
    """Open posts, open posts at 80+, and how many of those still lack a Claude rank."""
    init_db()
    with SessionLocal() as session:
        open_q = session.query(JobPost).filter(JobPost.status.notin_(INACTIVE_STATUSES))
        top_q = open_q.filter(JobPost.score >= _RERANK_MIN_SCORE)
        return {
            "open": open_q.count(),
            "top": top_q.count(),
            "waiting": top_q.filter(JobPost.global_rerank_position.is_(None)).count(),
        }


def _render_post_rows(posts: list[JobPost], source_map: dict[str, str]) -> str:
    post_rows = ""
    for p in posts:
        ef = p.extracted_fields or {}
        score_val = int(p.score) if p.score is not None else "-"
        color = _score_color(p.score)
        rationale = p.rerank_rationale or (p.score_breakdown.get("summary", "") if p.score_breakdown else "")
        location = ef.get("location_raw") or ""
        company = ef.get("company_name") or ""
        apply_url = p.apply_url or "#"
        source_label = source_map.get(p.source_id, p.source_id or "")
        posted = _fmt_date(p.posting_date or p.ingested_at)
        is_db = p.score_breakdown and p.score_breakdown.get("dealbreaker")
        db_badge = " <span style='color:#cf222e;font-size:11px'>[DB]</span>" if is_db else ""
        applied_badge = (
            " <span style='color:#1a7f37;font-size:11px'>[APPLIED]</span>"
            if p.status == "applied" else ""
        )
        rank = p.global_rerank_position
        rank_html = f"<span style='font-size:11px;color:#6b7280;font-weight:600'>#{rank}&nbsp;&nbsp;</span>" if rank else ""

        post_rows += f"""
        <tr style='border-bottom:1px solid #eee'>
          <td style='padding:8px 10px 8px 0;white-space:nowrap;vertical-align:top'>
            <span style='font-size:18px;font-weight:bold;color:{color}'>{score_val}</span>
          </td>
          <td style='padding:8px 16px 8px 0;vertical-align:top'>
            {rank_html}<a href='{_h(apply_url)}' style='color:#0969da;text-decoration:none;font-weight:600'>{_h(p.title)}</a>
            {applied_badge}
            {db_badge}
            <div style='font-size:12px;color:#666;margin-top:3px;line-height:1.4'>{_h(rationale)}</div>
          </td>
          <td style='padding:8px 16px 8px 0;font-size:13px;white-space:nowrap;vertical-align:top'>{_h(company)}</td>
          <td style='padding:8px 16px 8px 0;font-size:13px;color:#555;white-space:nowrap;vertical-align:top'>{_h(location)}</td>
          <td style='padding:8px 16px 8px 0;font-size:12px;color:#888;white-space:nowrap;vertical-align:top'>{_h(source_label)}</td>
          <td style='padding:8px 12px 8px 0;font-size:12px;color:#888;white-space:nowrap;vertical-align:top'>{posted}</td>
          <td style='padding:8px 0;white-space:nowrap;vertical-align:top'>
            <a href='{_h(apply_url)}' style='font-size:12px;color:#0969da;text-decoration:none;border:1px solid #0969da;border-radius:4px;padding:2px 8px;white-space:nowrap'>Apply →</a>
          </td>
        </tr>"""
    return post_rows


def _render_post_table(title: str, posts: list[JobPost], source_map: dict[str, str]) -> str:
    return f"""
<h3 style="margin:0 0 12px 0;font-size:13px;text-transform:uppercase;color:#666;letter-spacing:.05em">{_h(title)}</h3>
<table style="border-collapse:collapse;width:100%;font-size:13px;margin-bottom:28px">
  <thead>
    <tr style="border-bottom:2px solid #ddd">
      <th style="padding:6px 10px 6px 0;text-align:left;color:#666;font-weight:500">Score</th>
      <th style="padding:6px 16px 6px 0;text-align:left;color:#666;font-weight:500">Role</th>
      <th style="padding:6px 16px 6px 0;text-align:left;color:#666;font-weight:500">Company</th>
      <th style="padding:6px 16px 6px 0;text-align:left;color:#666;font-weight:500">Location</th>
      <th style="padding:6px 16px 6px 0;text-align:left;color:#666;font-weight:500">Source</th>
      <th style="padding:6px 12px 6px 0;text-align:left;color:#666;font-weight:500">Posted</th>
      <th style="padding:6px 0;text-align:left;color:#666;font-weight:500"></th>
    </tr>
  </thead>
  <tbody>{_render_post_rows(posts, source_map)}</tbody>
</table>"""


def build_html(
    new_posts: list[JobPost],
    new_source_map: dict[str, str],
    db_top_posts: list[JobPost],
    db_source_map: dict[str, str],
    days: int,
    top_n: int,
    status_html: str = "",
) -> str:
    now_str = datetime.now().strftime("%A, %d %B %Y")
    total = len(new_posts)
    scored = sum(1 for p in new_posts if p.score is not None)
    dealbreakers = sum(
        1 for p in new_posts
        if p.score_breakdown and p.score_breakdown.get("dealbreaker")
    )
    latest_top_posts = sorted(
        [p for p in new_posts if p.score is not None],
        key=lambda p: (
            p.global_rerank_position if p.global_rerank_position is not None else 99999,
            -(p.score or 0),
        ),
    )[:top_n]

    # Source breakdown
    source_counts: dict[str, int] = {}
    for p in new_posts:
        label = new_source_map.get(p.source_id, p.source_id or "unknown")
        source_counts[label] = source_counts.get(label, 0) + 1

    source_rows = "".join(
        f"<tr><td style='padding:3px 16px 3px 0'>{_h(src)}</td>"
        f"<td style='padding:3px 0'><strong>{count}</strong></td></tr>"
        for src, count in sorted(source_counts.items(), key=lambda x: -x[1])
    )

    db_top_table = _render_post_table(f"Top {len(db_top_posts)} in DB", db_top_posts, db_source_map)
    latest_top_table = _render_post_table(
        f"Latest top {len(latest_top_posts)} (last {days}d)", latest_top_posts, new_source_map
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Arial,sans-serif;color:#1f2328;padding:24px 32px">

<h2 style="margin:0 0 4px 0">JobTracker Daily Report</h2>
<p style="color:#666;margin:0 0 24px 0">{now_str} &mdash; last {days}d window</p>

{status_html}

<table style="border-collapse:collapse;margin-bottom:24px;background:#f6f8fa;padding:12px 16px;border-radius:6px">
  <tr>
    <td style="padding:4px 32px 4px 0">New posts</td>
    <td style="padding:4px 0"><strong>{total}</strong></td>
  </tr>
  <tr>
    <td style="padding:4px 32px 4px 0">Scored</td>
    <td style="padding:4px 0"><strong>{scored}</strong></td>
  </tr>
  <tr>
    <td style="padding:4px 32px 4px 0">Dealbreakers</td>
    <td style="padding:4px 0"><strong style="color:#cf222e">{dealbreakers}</strong></td>
  </tr>
</table>

<h3 style="margin:0 0 8px 0;font-size:13px;text-transform:uppercase;color:#666;letter-spacing:.05em">By source</h3>
<table style="border-collapse:collapse;margin-bottom:28px;font-size:13px">
  {source_rows}
</table>

{db_top_table}

{latest_top_table}

<p style="margin-top:32px;font-size:11px;color:#999">Generated by JobTracker &mdash; <a href="http://localhost:8501" style="color:#999">open UI</a></p>
</body>
</html>"""


def send_email(subject: str, html_body: str) -> None:
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.getenv("REPORT_RECIPIENT", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        server.sendmail(user, recipient, msg.as_string())
    print(f"Report sent to {recipient}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send daily job report email")
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS,
                        help=f"Lookback window in days (default: {_DEFAULT_DAYS})")
    parser.add_argument("--hours", type=int, default=None,
                        help="Lookback window in hours (overrides --days)")
    parser.add_argument("--top", type=int, default=_DEFAULT_TOP,
                        help=f"Max posts to list in 'latest' section (default: {_DEFAULT_TOP})")
    parser.add_argument("--db-top", type=int, default=_DEFAULT_DB_TOP,
                        help=f"Max posts to list in all-time DB section (default: {_DEFAULT_DB_TOP})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print HTML to stdout instead of sending email")
    parser.add_argument("--run-log", type=Path, default=None,
                        help="Pipeline run log to summarise in a status block (run_pipeline.sh "
                             "passes its own; pass an older one to rebuild that run's status)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Write the HTML to this file instead of sending (implies no email)")
    args = parser.parse_args()

    days = (args.hours / 24) if args.hours is not None else args.days
    posts, source_map = fetch_new_posts(days)
    db_top_posts, db_source_map = fetch_top_db_posts(args.db_top)
    window_label = f"{args.hours}h" if args.hours is not None else f"{args.days}d"
    print(f"New posts in last {window_label}: {len(posts)}", file=sys.stderr)

    marker, status_html = "", ""
    if args.run_log:
        try:
            run = parse_run_log(args.run_log, live=True)
            # A log that already has "Pipeline complete" is a past run being rebuilt
            note = ("" if run.finished is None else
                    f"Rebuilt after the fact from {args.run_log.name}. The pipeline line and "
                    "job lists below show the database now, not at the time of the run.")
            status_html = render_status_html(run, fetch_pipeline_counts(), note=note)
            marker = f" {subject_marker(run)}"
        except Exception as exc:  # a status-block bug must never block the report
            print(f"WARNING: could not build run status: {exc}", file=sys.stderr)
            marker = " ❔ status unavailable"

    subject = f"JobTracker{marker}: {len(posts)} new posts — {datetime.now().strftime('%d %b %Y')}"
    html_body = build_html(posts, source_map, db_top_posts, db_source_map, int(days), args.top,
                           status_html=status_html)

    if args.out:
        args.out.write_text(html_body)
        print(f"Subject: {subject}\nWrote {args.out}", file=sys.stderr)
        return

    if args.dry_run:
        print(html_body)
        return

    if not os.getenv("GMAIL_USER") or not os.getenv("GMAIL_APP_PASSWORD"):
        print("ERROR: GMAIL_USER and GMAIL_APP_PASSWORD must be set in .env", file=sys.stderr)
        sys.exit(1)

    send_email(subject, html_body)


if __name__ == "__main__":
    main()
