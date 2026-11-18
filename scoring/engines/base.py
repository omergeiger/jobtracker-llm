from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ScoreResult:
    score: float                          # 0–100
    summary: str
    domain_match: str = ""
    location_match: str = ""
    role_match: str = ""
    bonus_signals: list[str] = field(default_factory=list)
    dealbreaker: bool = False
    dealbreaker_reason: str = ""
    engine: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "summary": self.summary,
            "domain_match": self.domain_match,
            "location_match": self.location_match,
            "role_match": self.role_match,
            "bonus_signals": self.bonus_signals,
            "dealbreaker": self.dealbreaker,
            "dealbreaker_reason": self.dealbreaker_reason,
            "engine": self.engine,
        }


class ScoringEngine(ABC):
    @abstractmethod
    def score(self, post: dict) -> ScoreResult:
        """Score a single job post.

        post keys: title, company_name, location_raw, remote_type,
                   office_days, seniority_level, department, raw_description
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
