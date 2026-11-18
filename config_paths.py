"""Where per-user state lives.

Set JOBTRACKER_HOME to a folder that contains the per-user files:

    user_profile.yaml     preferences, read by the LLM engines
    scoring_rules.yaml    keyword rules, read by the RulesEngine
    cv_structured.json    structured CV, read by the LLM engines
    companies.yaml        companies to fetch, per ATS (optional; read by sourcing)
    jobtracker.db         SQLite database

Without companies.yaml, sourcing falls back to the shipped registry in
sourcing/source_registry/ (the demo persona's companies).

e.g. `JOBTRACKER_HOME=examples/dana_example streamlit run ui/app.py` runs against the bundled demo.
A relative path is resolved against the current working directory.

When JOBTRACKER_HOME is unset, the defaults are `resources/` for the config files and
`data/` for the database.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

_home = os.environ.get("JOBTRACKER_HOME", "").strip()

if _home:
    HOME = Path(_home).expanduser().resolve()
    PROFILE_PATH = HOME / "user_profile.yaml"
    RULES_PATH = HOME / "scoring_rules.yaml"
    CV_PATH = HOME / "cv_structured.json"
    COMPANIES_PATH = HOME / "companies.yaml"
    DB_PATH = HOME / "jobtracker.db"
else:
    HOME = None
    PROFILE_PATH = REPO_ROOT / "resources" / "user_profile.yaml"
    RULES_PATH = REPO_ROOT / "resources" / "scoring_rules.yaml"
    CV_PATH = REPO_ROOT / "resources" / "cv_structured.json"
    COMPANIES_PATH = REPO_ROOT / "resources" / "companies.yaml"
    DB_PATH = REPO_ROOT / "data" / "jobtracker.db"
