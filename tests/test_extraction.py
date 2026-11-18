"""C1a - rule-based field extraction on known strings (verifiable)."""

import pytest

from ingestion.extraction.rules import (
    _extract_office_days,
    _extract_remote_type,
    _extract_seniority,
)


@pytest.mark.parametrize("text, expected", [
    ("tel aviv, israel. hybrid work model, 3 days a week in office", "hybrid"),
    ("hybrid - remote friendly", "hybrid"),                # hybrid wins over remote
    ("this is a fully remote position", "remote"),
    ("we are a remote-first company", "remote"),
    ("work from anywhere", "remote"),
    ("this role is on-site in our haifa office", "onsite"),
    ("in-office five days a week", "onsite"),
    ("tel aviv, israel. build backend services.", "unknown"),
])
def test_remote_type(text, expected):
    assert _extract_remote_type(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("hybrid, 2 days per week in office", "2 days/week"),
    ("3 days in-office", "3 days/week"),
    ("hybrid model: 4 days/week on-site", "4 days/week"),
    ("hybrid, flexible schedule", None),
])
def test_office_days(text, expected):
    assert _extract_office_days(text) == expected


@pytest.mark.parametrize("title, expected", [
    ("Director of Engineering", "director"),
    ("VP R&D", "vp"),
    ("Principal Software Engineer", "principal"),
    ("Staff Backend Engineer", "staff"),
    ("Tech Lead, Payments", "lead"),
    ("Senior Backend Engineer", "senior"),
    ("Sr. Software Engineer", "senior"),
    ("Junior Developer", "junior"),
    ("Engineering Manager", "manager"),
    ("Backend Engineer", "mid"),
    ("Senior Engineering Manager", "senior"),   # priority order: senior before manager
])
def test_seniority(title, expected):
    assert _extract_seniority(title) == expected
