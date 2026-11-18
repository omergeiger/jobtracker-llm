import re
from bs4 import BeautifulSoup

from ingestion.models import RawJobPost


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(separator=" ").lower()


def _search_text(*sources: str) -> str:
    """Concatenate all text sources into one lowercase blob for pattern matching."""
    return " ".join(s.lower() for s in sources if s)


# ── Field extractors ──────────────────────────────────────────────────────────

_REMOTE_SIGNALS = re.compile(
    r"\bfully[ -]?remote\b|"
    r"\b100%[ -]?remote\b|"
    r"\bremote[ -]?first\b|"
    r"\bremote[ -]?friendly\b|"
    r"\bwork from (home|anywhere)\b|"
    r"\bwfh\b"
)

_ONSITE_SIGNALS = re.compile(
    r"\bon[ -]?site\b|"
    r"\bin[ -]?office\b|"
    r"\bon[ -]?location\b"
)

_HYBRID_SIGNALS = re.compile(r"\bhybrid\b")

_OFFICE_DAYS = re.compile(
    r"(\d+)\s*(?:days?)\s*(?:per\s*week|/\s*week|a\s*week)?\s*(?:in[- ]?office|on[- ]?site|in[- ]?person)?|"
    r"(\d+)\s*days?\s*(?:in[- ]?office|on[- ]?site)"
)

_SENIORITY_MAP = [
    (re.compile(r"\bdirector\b"), "director"),
    (re.compile(r"\bvp\b|\bvice president\b"), "vp"),
    (re.compile(r"\bprincipal\b"), "principal"),
    (re.compile(r"\bstaff\b"), "staff"),
    (re.compile(r"\blead\b"), "lead"),
    (re.compile(r"\bsenior\b|\bsr\.?\b"), "senior"),
    (re.compile(r"\bjunior\b|\bjr\.?\b"), "junior"),
    (re.compile(r"\bmanager\b"), "manager"),
]


def _extract_remote_type(text: str) -> str:
    if _HYBRID_SIGNALS.search(text):
        return "hybrid"
    if _REMOTE_SIGNALS.search(text):
        return "remote"
    if _ONSITE_SIGNALS.search(text):
        return "onsite"
    # Greenhouse location heuristic: if location says a city with no remote hint → onsite
    return "unknown"


def _extract_office_days(text: str) -> str | None:
    match = _OFFICE_DAYS.search(text)
    if not match:
        return None
    days = match.group(1) or match.group(2)
    return f"{days} days/week" if days else None


def _extract_seniority(title: str) -> str:
    title_lower = title.lower()
    for pattern, label in _SENIORITY_MAP:
        if pattern.search(title_lower):
            return label
    return "mid"


def _extract_department(raw: RawJobPost) -> str | None:
    departments = raw.metadata.get("departments", [])
    if departments:
        return departments[0].get("name")
    return None


# ── Public interface ──────────────────────────────────────────────────────────

def extract_fields(raw: RawJobPost) -> dict:
    """
    Rule-based extraction for structured sources (e.g. Greenhouse).
    Returns a dict stored as extracted_fields on JobPost.
    Fields are best-effort; unknown values are None rather than guessed.
    """
    description_text = _strip_html(raw.raw_description)
    search_blob = _search_text(raw.location_raw, description_text)

    remote_type = _extract_remote_type(search_blob)
    office_days = _extract_office_days(search_blob) if remote_type == "hybrid" else None

    return {
        "title": raw.title,
        "company_name": raw.company_name,
        "location_raw": raw.location_raw,
        "remote_type": remote_type,
        "office_days": office_days,
        "seniority_level": _extract_seniority(raw.title),
        "department": _extract_department(raw),
        "description_text": BeautifulSoup(raw.raw_description, "html.parser").get_text(separator="\n").strip(),
    }
