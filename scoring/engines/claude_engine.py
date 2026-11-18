import json
import logging
import os
import subprocess
import tempfile

from bs4 import BeautifulSoup

from scoring.engines.base import ScoringEngine, ScoreResult
from scoring.engines.llm_prompt import base_system_prompt

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "haiku"
_TIMEOUT_S = 120

# Claude enforces the response shape via --json-schema, so unlike Ollama/HuggingFace
# it doesn't need the JSON shape spelled out in the prompt itself.
_STRUCTURED_OUTPUT_INSTRUCTIONS = "\n\nProvide your evaluation as structured output matching the given schema."

_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "summary": {"type": "string"},
        "domain_match": {"type": "string"},
        "location_match": {"type": "string"},
        "role_match": {"type": "string"},
        "bonus_signals": {"type": "array", "items": {"type": "string"}},
        "dealbreaker": {"type": "boolean"},
        "dealbreaker_reason": {"type": "string"},
    },
    "required": [
        "score", "summary", "domain_match", "location_match",
        "role_match", "bonus_signals", "dealbreaker", "dealbreaker_reason",
    ],
})


class ClaudeEngine(ScoringEngine):
    # Shells out to the Claude Code CLI in headless mode (`claude -p`), billed against
    # the caller's subscription login rather than a metered API key. ~$0.04-0.12 and
    # 20-55s per post - orders of magnitude slower/costlier than Ollama or rules. Meant
    # for refining a small set of already-promising posts, not bulk scoring.

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        self._model = model
        self._system_prompt = base_system_prompt() + _STRUCTURED_OUTPUT_INSTRUCTIONS
        # Cost/timing from the most recent score() call (CLI's own reported figures,
        # not wall-clock) — read by callers that need per-call cost, e.g. ad-hoc
        # comparison scripts. Re-set on every call; None until the first call completes.
        self.last_cost_usd: float | None = None
        self.last_duration_ms: int | None = None

    @property
    def name(self) -> str:
        return f"claude/{self._model}"

    def score(self, post: dict) -> ScoreResult:
        user_msg = self._format_post(post)
        env = dict(os.environ)
        if env.pop("ANTHROPIC_API_KEY", None):
            logger.warning(
                "ANTHROPIC_API_KEY was set in the environment; stripping it so the "
                "claude CLI can only authenticate via the subscription login"
            )
        result = subprocess.run(
            [
                "claude", "-p", user_msg,
                "--system-prompt", self._system_prompt,
                "--model", self._model,
                "--output-format", "json",
                "--json-schema", _SCHEMA,
                "--tools", "",
                "--no-session-persistence",
                "--setting-sources", "",
                "--strict-mcp-config",
            ],
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            timeout=_TIMEOUT_S,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr[:300]}")

        data = json.loads(result.stdout)
        if data.get("is_error"):
            raise RuntimeError(f"claude CLI error: {str(data.get('result', ''))[:300]}")

        self.last_cost_usd = data.get("total_cost_usd", 0.0)
        self.last_duration_ms = data.get("duration_ms", 0)
        logger.debug(
            "Claude call cost=$%.4f duration=%dms",
            self.last_cost_usd, self.last_duration_ms,
        )
        return self._parse(data.get("structured_output") or {})

    def _format_post(self, post: dict) -> str:
        desc_plain = BeautifulSoup(
            post.get("raw_description", ""), "html.parser"
        ).get_text(separator=" ")
        desc = desc_plain[:2000].strip()
        return (
            f"Title: {post.get('title', 'Unknown')}\n"
            f"Company: {post.get('company_name', 'Unknown')}\n"
            f"Location: {post.get('location_raw', 'Unknown')}\n"
            f"Remote type: {post.get('remote_type', 'unknown')}\n"
            f"Office days: {post.get('office_days') or 'not specified'}\n"
            f"Seniority (extracted): {post.get('seniority_level', 'unknown')}\n"
            f"Department: {post.get('department') or 'unknown'}\n\n"
            f"Job Description:\n{desc}"
        )

    def _parse(self, data: dict) -> ScoreResult:
        return ScoreResult(
            score=float(max(0, min(100, data.get("score", 50)))),
            summary=str(data.get("summary", "")),
            domain_match=str(data.get("domain_match", "")),
            location_match=str(data.get("location_match", "")),
            role_match=str(data.get("role_match", "")),
            bonus_signals=list(data.get("bonus_signals") or []),
            dealbreaker=bool(data.get("dealbreaker", False)),
            dealbreaker_reason=str(data.get("dealbreaker_reason", "")),
            engine=self.name,
        )
