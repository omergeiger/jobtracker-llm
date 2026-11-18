"""C1b - RulesEngine behavior (verifiable).

Two kinds of checks, both against the committed demo persona rules (examples/dana_example):
- explicit expectations that encode the intended behavior (early exits, ordering), and
- a golden snapshot of every fixture post's full score breakdown, so any unintended change
  to the engine or the demo rules fails loudly. Regenerate deliberately with:
      UPDATE_SNAPSHOT=1 pytest tests/test_rules_engine.py
"""

import json
import os
from pathlib import Path

import pytest

from scoring.engines.rules import RulesEngine

FIXTURES = Path(__file__).parent / "fixtures"
POSTS = {p["id"]: p for p in json.loads((FIXTURES / "posts.json").read_text())}
SNAPSHOT = FIXTURES / "rules_snapshot.json"


@pytest.fixture(scope="module")
def engine():
    return RulesEngine()


def score(engine, post_id):
    return engine.score(POSTS[post_id])


# ── Intended behavior ─────────────────────────────────────────────────────────

def test_intern_title_is_dealbreaker(engine):
    r = score(engine, "intern")
    assert r.dealbreaker and r.score == 5


def test_clearance_is_dealbreaker(engine):
    r = score(engine, "clearance")
    assert r.dealbreaker and "clearance" in r.dealbreaker_reason.lower()


def test_frontend_only_is_dealbreaker_but_full_stack_is_not(engine):
    assert score(engine, "frontend_only").dealbreaker
    assert not score(engine, "frontend_and_backend").dealbreaker


def test_excluded_function_title_scores_15(engine):
    for post_id in ("recruiter_title", "data_scientist_title"):
        r = score(engine, post_id)
        assert r.score == 15 and not r.dealbreaker


def test_location_ordering(engine):
    """Same role, different location: preferred area > home country > worldwide remote > restricted > abroad."""
    tlv = score(engine, "tlv_hybrid_backend_senior").score
    worldwide = score(engine, "remote_worldwide").score
    us_only = score(engine, "remote_us_only").score
    berlin = score(engine, "onsite_berlin").score
    assert tlv > worldwide > us_only > berlin


def test_office_day_limit_is_penalized(engine):
    within = score(engine, "tlv_hybrid_backend_senior")
    over = score(engine, "tlv_hybrid_4_days")
    assert "above preferred limit" in over.location_match
    assert "above" not in within.location_match


def test_penalty_applies(engine):
    assert "legacy" in score(engine, "legacy_penalty").role_match.lower()


def test_scores_are_bounded(engine):
    for post in POSTS.values():
        assert 0 <= engine.score(post).score <= 100


# ── Golden snapshot ───────────────────────────────────────────────────────────

def test_snapshot(engine):
    actual = {pid: engine.score(p).to_dict() for pid, p in POSTS.items()}
    if os.environ.get("UPDATE_SNAPSHOT") or not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(actual, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
        pytest.skip("snapshot written")
    expected = json.loads(SNAPSHOT.read_text())
    changed = {pid for pid in expected if expected[pid] != actual.get(pid)}
    assert not changed, f"rules output changed for: {sorted(changed)}"
