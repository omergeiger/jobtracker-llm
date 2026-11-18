"""The per-user companies.yaml replaces the shipped registry; without it, the registry is used."""

import config_paths
from sourcing.source_registry import load_confirmed


def test_registry_used_without_companies_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config_paths, "COMPANIES_PATH", tmp_path / "companies.yaml")
    assert load_confirmed("greenhouse")  # shipped demo registry is not empty


def test_companies_file_replaces_registry(tmp_path, monkeypatch):
    path = tmp_path / "companies.yaml"
    path.write_text("greenhouse:\n  - {slug: acme, name: Acme}\n")
    monkeypatch.setattr(config_paths, "COMPANIES_PATH", path)
    assert load_confirmed("greenhouse") == [{"slug": "acme", "name": "Acme", "notes": "", "location": ""}]
    assert load_confirmed("lever") == []  # absent source: nothing, not the shipped list
