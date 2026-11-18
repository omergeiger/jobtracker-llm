import json
import logging

from bs4 import BeautifulSoup

from scoring.engines.base import ScoringEngine, ScoreResult
from scoring.engines.llm_prompt import base_system_prompt, JSON_RESPONSE_INSTRUCTIONS

logger = logging.getLogger(__name__)

_PREFERRED_MODEL = "qwen2.5:7b"
_FALLBACK_MODEL = "llama3.2:latest"

# Avoid llama3.1:70b via --model: Ollama defaults to its full 131072-token trained
# context, and the resulting KV cache pushes total memory past 100GB - too much for
# a 64GB unified-memory Mac. The runner crashes mid-request ("model runner has
# unexpectedly stopped") rather than degrading gracefully.


class OllamaEngine(ScoringEngine):

    def __init__(self, model: str = _PREFERRED_MODEL) -> None:
        import ollama  # deferred so missing package only errors here
        self._ollama = ollama
        self._model = self._resolve_model(model)
        self._system_prompt = base_system_prompt() + JSON_RESPONSE_INSTRUCTIONS

    @property
    def name(self) -> str:
        return f"ollama/{self._model}"

    def _resolve_model(self, preferred: str) -> str:
        try:
            available = {m.model for m in self._ollama.list().models}
            if preferred in available:
                return preferred
            if _FALLBACK_MODEL in available:
                logger.warning("Model %s not found; using fallback %s", preferred, _FALLBACK_MODEL)
                return _FALLBACK_MODEL
            if available:
                chosen = next(iter(available))
                logger.warning("Neither preferred model found; using %s", chosen)
                return chosen
        except Exception as exc:
            logger.warning("Could not list Ollama models: %s", exc)
        return preferred

    def score(self, post: dict) -> ScoreResult:
        user_msg = self._format_post(post)
        resp = self._ollama.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_msg},
            ],
            format="json",
            options={"temperature": 0.1},
        )
        data = json.loads(resp.message.content)
        return self._parse(data)

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
        score = float(max(0, min(100, data.get("score", 50))))
        return ScoreResult(
            score=score,
            summary=str(data.get("summary", "")),
            domain_match=str(data.get("domain_match", "")),
            location_match=str(data.get("location_match", "")),
            role_match=str(data.get("role_match", "")),
            bonus_signals=list(data.get("bonus_signals") or []),
            dealbreaker=bool(data.get("dealbreaker", False)),
            dealbreaker_reason=str(data.get("dealbreaker_reason", "")),
            engine=self.name,
        )
