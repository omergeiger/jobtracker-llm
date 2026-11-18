"""
Source registry loader.

Which companies to fetch comes from the per-user `companies.yaml`
(config_paths.COMPANIES_PATH) when it exists: one top-level key per source, each a list of
entries with `slug`, `name` and optional `notes` / `location`. The build-profile skill writes it.

Without it, the shipped per-source YAML files in sourcing/source_registry/ are used (their
`confirmed` sections cover the demo persona). Those files also hold per-source settings
(`enabled`) and `candidates`, which stay in use either way.
"""

from pathlib import Path
from typing import TypedDict

import yaml

import config_paths

REGISTRY_DIR = Path(__file__).parent / "source_registry"


class CompanyEntry(TypedDict):
    slug: str
    name: str
    notes: str    # optional; empty string when absent
    location: str # optional; empty string when absent


def load_confirmed(source_id: str) -> list[CompanyEntry]:
    """Return the companies to fetch for a source.

    From the per-user companies.yaml when it exists (a source missing from it has no
    companies); otherwise from the shipped registry's `confirmed` section.
    """
    if config_paths.COMPANIES_PATH.exists():
        data = yaml.safe_load(config_paths.COMPANIES_PATH.read_text()) or {}
        entries = data.get(source_id) or []
    else:
        path = REGISTRY_DIR / f"{source_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"No registry file for source '{source_id}': {path}")
        data = yaml.safe_load(path.read_text())
        entries = data.get("confirmed") or []
    return [
        CompanyEntry(
            slug=e["slug"],
            name=e["name"],
            notes=e.get("notes", ""),
            location=e.get("location", ""),
        )
        for e in entries
    ]


def load_candidates(source_id: str) -> list[CompanyEntry]:
    """Return candidate entries for a source (Israel-relevant only)."""
    path = REGISTRY_DIR / f"{source_id}.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text())
    entries = data.get("candidates") or []
    return [
        CompanyEntry(
            slug=e.get("slug", ""),
            name=e["name"],
            notes=e.get("notes", ""),
            location=e.get("location", ""),
        )
        for e in entries
    ]


def load_prospecting() -> list[CompanyEntry]:
    """Return companies in prospecting.yaml — Israeli targets with unknown ATS."""
    path = REGISTRY_DIR / "prospecting.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text())
    entries = data.get("companies") or []
    return [
        CompanyEntry(
            slug="",
            name=e["name"],
            notes=e.get("notes", ""),
            location="",
        )
        for e in entries
    ]


def list_sources() -> list[str]:
    """Return enabled source IDs. A registry file with `enabled: false` is skipped."""
    return sorted(
        p.stem for p in REGISTRY_DIR.glob("*.yaml")
        if not p.stem.endswith("_intl") and p.stem != "prospecting"
        and (yaml.safe_load(p.read_text()) or {}).get("enabled", True)
    )
