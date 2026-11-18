import json
import logging
import os
import re
import time

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from scoring.engines.base import ScoringEngine, ScoreResult
from scoring.engines.llm_prompt import base_system_prompt, JSON_RESPONSE_INSTRUCTIONS

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = os.getenv("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
_DEFAULT_PROVIDER = os.getenv("HF_PROVIDER", "featherless-ai")
_API_URL = "https://router.huggingface.co/{provider}/v1/chat/completions"
_MAX_TOKENS = 600
_TIMEOUT_S = 90
_RETRY_WAIT_S = 20


class HuggingFaceEngine(ScoringEngine):

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        token = os.getenv("HF_API_TOKEN", "")
        if not token or not token.startswith("hf_"):
            raise EnvironmentError(
                "HF_API_TOKEN not set or invalid — must start with 'hf_'. Check your .env file."
            )
        self._model = model
        self._url = _API_URL.format(provider=_DEFAULT_PROVIDER)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._system_prompt = base_system_prompt() + JSON_RESPONSE_INSTRUCTIONS
        logger.info("HuggingFaceEngine ready — model: %s", self._model)

    @property
    def name(self) -> str:
        return f"huggingface/{self._model.split('/')[-1]}"

    def score(self, post: dict) -> ScoreResult:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": self._format_post(post)},
            ],
            "max_tokens": _MAX_TOKENS,
            "temperature": 0.1,
            "stream": False,
        }
        resp = self._post_with_retry(payload)
        content = resp["choices"][0]["message"]["content"]
        return self._parse(_extract_json(content))

    def _post_with_retry(self, payload: dict) -> dict:
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            r = client.post(self._url, headers=self._headers, json=payload)
            if r.status_code == 503:
                wait = r.json().get("estimated_time", _RETRY_WAIT_S)
                logger.info("Model loading, retrying in %ss", wait)
                time.sleep(wait)
                r = client.post(self._url, headers=self._headers, json=payload)
            r.raise_for_status()
            return r.json()

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


def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON from HF response: {text[:300]}")
