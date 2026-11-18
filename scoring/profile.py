from pathlib import Path
import json
import yaml

from config_paths import CV_PATH, PROFILE_PATH, RULES_PATH

GUIDE_PATH = Path(__file__).parent / "scoring_guide.md"


def _require(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing per-user file: {path}\n"
            "Create your own with the build-profile skill, or try the bundled demo with "
            "JOBTRACKER_HOME=examples/dana_example."
        )
    return path


def load_profile() -> dict:
    return yaml.safe_load(_require(PROFILE_PATH).read_text())


def load_scoring_rules() -> dict:
    return yaml.safe_load(_require(RULES_PATH).read_text())


def load_cv() -> dict:
    return json.loads(_require(CV_PATH).read_text())


def profile_as_text() -> str:
    return _require(PROFILE_PATH).read_text()


def scoring_guide_as_text() -> str:
    return GUIDE_PATH.read_text()


def cv_summary() -> str:
    cv = load_cv()
    recent = (cv.get("experience") or [{}])[0]
    education = (cv.get("education") or [{}])[0]
    profile_text = cv.get("professional_profile", "")[:300]
    return (
        f"Name: {cv['name']}\n"
        f"Current role: {recent.get('title', '')} at {recent.get('company', '')} "
        f"({recent.get('years', '')})\n"
        f"Education: {education.get('degree', '')}\n"
        f"Summary: {profile_text}..."
    )
