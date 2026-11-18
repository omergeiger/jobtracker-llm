"""Shared test setup.

Points JOBTRACKER_HOME at a fresh temp folder holding the demo persona's config files
(before any project module is imported), so tests never touch real per-user data and
always score against the same, committed demo rules.
"""

import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "dana_example"

_home = Path(tempfile.mkdtemp(prefix="jobtracker_test_"))
for name in ("user_profile.yaml", "scoring_rules.yaml", "cv_structured.json"):
    shutil.copy(DEMO / name, _home / name)
os.environ["JOBTRACKER_HOME"] = str(_home)
