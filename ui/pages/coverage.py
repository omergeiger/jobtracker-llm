"""
Coverage — source and company pipeline view.

Auto-discovered by Streamlit as a second page alongside ui/app.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import streamlit as st

from sourcing.source_registry import list_sources, load_candidates, load_prospecting
from ui.queries import get_coverage_stats

st.set_page_config(page_title="Coverage | Job Tracker", layout="wide")
st.title("Source Coverage")

# ── Active companies ────────────────────────────────────────────────────────

st.subheader("Active companies")
rows = get_coverage_stats()

if not rows:
    st.info("No posts in DB yet.")
else:
    df = pd.DataFrame(rows)

    c1, c2, c3 = st.columns(3)
    c1.metric("Companies", df["Company"].nunique())
    c2.metric("Total jobs", int(df["Jobs"].sum()))
    c3.metric("Sources", df["Source"].nunique())

    st.dataframe(df, use_container_width=True, hide_index=True)

# ── Candidate pipeline ──────────────────────────────────────────────────────

st.subheader("Candidate pipeline")
pipeline = []

for source_id in list_sources():
    if source_id == "jobspy":
        continue  # jobspy candidates are search queries, not companies
    for entry in load_candidates(source_id):
        if source_id == "comeet":
            status = "Need uid/token" if not entry["slug"] else "Slug untested"
        else:
            status = "Slug untested"
        pipeline.append({
            "Company": entry["name"],
            "Platform": source_id,
            "Status": status,
            "Notes": entry["notes"],
        })

for entry in load_prospecting():
    pipeline.append({
        "Company": entry["name"],
        "Platform": "—",
        "Status": "ATS unknown",
        "Notes": entry["notes"],
    })

if pipeline:
    st.dataframe(pd.DataFrame(pipeline), use_container_width=True, hide_index=True)
else:
    st.info("No candidates in pipeline.")
