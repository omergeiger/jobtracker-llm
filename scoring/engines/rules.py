"""RulesEngine: fast keyword scoring driven entirely by the per-user `scoring_rules.yaml`.

The structure is fixed (domain + location + seniority + bonus - penalty, with title and
dealbreaker early exits). Every keyword, label and point value comes from the config, so
no user preferences live in this file. See `scoring_rules.yaml` for the schema.
"""

import re

from bs4 import BeautifulSoup

from scoring.engines.base import ScoringEngine, ScoreResult
from scoring.profile import load_scoring_rules


def _strip_html(text: str) -> str:
    return BeautifulSoup(text, "html.parser").get_text(separator=" ")


def _search_text(*parts: str) -> str:
    return " ".join(p.lower() for p in parts if p)


def _any_in(keywords: list[str], text: str) -> bool:
    return any(kw in text for kw in keywords)


class RulesEngine(ScoringEngine):

    def __init__(self, rules: dict | None = None) -> None:
        self._rules = rules if rules is not None else load_scoring_rules()
        r = self._rules
        self._hard_title_re = re.compile(r["title_exclusions"]["hard"]["regex"], re.IGNORECASE)
        self._soft_title_res = [
            (phrase, re.compile(r"(?<![a-z\-])" + re.escape(phrase.lower())))
            for phrase in r["title_exclusions"]["soft"]["phrases"]
        ]
        remote = r["location"]["remote"]
        self._worldwide_re = re.compile(remote["worldwide_regex"])
        self._restricted_res = {
            region: re.compile(pattern) for region, pattern in remote["restricted_regions"].items()
        }

    @property
    def name(self) -> str:
        return "rules"

    def score(self, post: dict) -> ScoreResult:
        title = post.get("title", "") or ""
        desc_plain = _strip_html(post.get("raw_description", ""))
        text = _search_text(title, post.get("location_raw", ""), desc_plain)

        hard = self._rules["title_exclusions"]["hard"]
        if self._hard_title_re.search(title):
            return ScoreResult(
                score=float(hard["score"]),
                summary=f"Dealbreaker: {hard['reason']}",
                dealbreaker=True,
                dealbreaker_reason=hard["reason"],
                engine=self.name,
            )

        db_reason = self._check_dealbreaker(text)
        if db_reason:
            return ScoreResult(
                score=float(self._rules["dealbreakers"]["score"]),
                summary=f"Dealbreaker: {db_reason}",
                dealbreaker=True,
                dealbreaker_reason=db_reason,
                engine=self.name,
            )

        nt_reason = self._check_excluded_title(title.lower())
        if nt_reason:
            return ScoreResult(
                score=float(self._rules["title_exclusions"]["soft"]["score"]),
                summary=f"Non-technical function — outside target role type ({nt_reason})",
                role_match=f"Non-technical function: {nt_reason}",
                engine=self.name,
            )

        domain_score, domain_notes = self._score_domain(text)
        loc_score, loc_notes = self._score_location(post)
        role_score, role_notes = self._score_role(post)
        bonus_score, bonus_signals = self._score_bonus(text)
        penalty, penalty_note = self._score_penalty(text)
        if penalty_note:
            role_notes = f"{role_notes}; {penalty_note}"

        total = float(max(0, min(100, domain_score + loc_score + role_score + bonus_score - penalty)))
        summary = self._build_summary(total, domain_score, loc_score, role_score, bonus_signals)

        return ScoreResult(
            score=total,
            summary=summary,
            domain_match=domain_notes,
            location_match=loc_notes,
            role_match=role_notes,
            bonus_signals=bonus_signals,
            engine=self.name,
        )

    # ── Early exits ───────────────────────────────────────────────────────────

    def _check_dealbreaker(self, text: str) -> str:
        for rule in self._rules["dealbreakers"]["rules"]:
            if _any_in(rule["any_of"], text) and not _any_in(rule.get("unless_any_of", []), text):
                return rule["reason"]
        return ""

    def _check_excluded_title(self, title_lower: str) -> str:
        # Negative lookbehind: the phrase must not follow a letter or hyphen,
        # so "pre-sales manager" doesn't trigger on "sales manager".
        for phrase, pattern in self._soft_title_res:
            if pattern.search(title_lower):
                return phrase.strip()
        return ""

    # ── Domain ────────────────────────────────────────────────────────────────

    def _score_domain(self, text: str) -> tuple[int, str]:
        d = self._rules["domains"]
        primary = d["primary"]
        if _any_in(primary["keywords"], text):
            return primary["points"], f"Primary domain match — {primary['label']}"

        demote = d.get("demote") or {}
        is_demoted = _any_in(demote.get("keywords", []), text)

        for area in d["open_to"]["areas"]:
            if _any_in(area["keywords"], text):
                if is_demoted and area["label"] in demote.get("applies_to", []):
                    return demote["points"], demote["note"]
                return d["open_to"]["points"], f"Open-to domain match — {area['label']}"

        if _any_in(d["weak"]["keywords"], text):
            return d["weak"]["points"], d["weak"]["note"]

        return d["none"]["points"], d["none"]["note"]

    # ── Location ──────────────────────────────────────────────────────────────

    def _score_location(self, post: dict) -> tuple[int, str]:
        cfg = self._rules["location"]
        loc = post.get("location_raw", "").lower()
        remote_type = post.get("remote_type", "unknown")
        office_days_str = post.get("office_days") or ""

        office_days: int | None = None
        if office_days_str:
            m = re.search(r"(\d+)", office_days_str)
            if m:
                office_days = int(m.group(1))

        for tier in cfg["tiers"]:
            if _any_in(tier["keywords"], loc):
                return self._score_tier(tier, remote_type, office_days)

        home = cfg["home_region"]
        if _any_in(home["keywords"], loc):
            return home["score"], home["note"]

        remote = cfg["remote"]
        restricted_region = next(
            (region for region, pattern in self._restricted_res.items() if pattern.search(loc)),
            None,
        )

        if self._worldwide_re.search(loc) and remote_type == "remote":
            return remote["worldwide"]["score"], remote["worldwide"]["note"]

        if restricted_region is None and remote_type == "remote":
            return remote["unclear"]["score"], remote["unclear"]["note"]

        if restricted_region is not None:
            r = remote["restricted"]
            return r["score"], r["note"].format(region=restricted_region)

        return cfg["elsewhere"]["score"], cfg["elsewhere"]["note"]

    @staticmethod
    def _score_tier(tier: dict, remote_type: str, office_days: int | None) -> tuple[int, str]:
        if "rules" not in tier:
            return tier["score"], tier["note"]

        rules = tier["rules"]
        max_days = tier.get("max_office_days")
        within = office_days is None or max_days is None or office_days <= max_days

        key = "default"
        if remote_type == "hybrid":
            key = "hybrid_within_limit" if within else "hybrid_over_limit"
        elif remote_type == "onsite":
            key = "onsite"
        chosen = rules.get(key, rules["default"])

        days_label = f"{office_days} days/week" if office_days else "unknown days"
        return chosen["score"], chosen["note"].format(days=days_label)

    # ── Seniority ─────────────────────────────────────────────────────────────

    def _score_role(self, post: dict) -> tuple[int, str]:
        cfg = self._rules["seniority"]
        level = post.get("seniority_level", "mid")
        entry = cfg["levels"].get(level)
        if entry is None:
            return cfg["unknown"]["points"], cfg["unknown"]["note"].format(level=level)
        return entry["points"], entry["note"]

    # ── Bonus / penalty ───────────────────────────────────────────────────────

    def _score_bonus(self, text: str) -> tuple[int, list[str]]:
        cfg = self._rules["bonus"]
        signals, total = [], 0
        for sig in cfg["signals"]:
            if _any_in(sig["keywords"], text):
                signals.append(sig["label"])
                total += sig["points"]
        return min(total, cfg["cap"]), signals

    def _score_penalty(self, text: str) -> tuple[int, str]:
        for p in self._rules.get("penalties") or []:
            if _any_in(p["keywords"], text):
                return p["points"], p["reason"]
        return 0, ""

    # ── Summary ───────────────────────────────────────────────────────────────

    def _build_summary(
        self, total: float, domain: int, location: int, role: int, bonus: list[str]
    ) -> str:
        if total >= 75:
            lead = "Strong match"
        elif total >= 55:
            lead = "Moderate match"
        elif total >= 35:
            lead = "Weak match"
        else:
            lead = "Poor match"

        positives: list[str] = []
        negatives: list[str] = []

        domains = self._rules["domains"]
        if domain >= domains["primary"]["points"]:
            positives.append(f"primary domain ({domains['primary']['label']})")
        elif domain >= domains["open_to"]["points"]:
            positives.append("adjacent domain")
        else:
            negatives.append("domain mismatch")

        if location >= 22:
            positives.append("preferred location")
        elif location >= 15:
            positives.append("acceptable location")
        elif location >= 6:
            negatives.append("remote with geographic restrictions")
        else:
            negatives.append("location outside preferences")

        if role >= 18:
            positives.append("target seniority/role type")
        elif role >= 14:
            positives.append("senior-level role")
        else:
            negatives.append("below target seniority")

        if bonus:
            positives.append(f"{len(bonus)} bonus signal(s)")

        pos = ", ".join(positives) or "no strong positives"
        neg = f" Gaps: {', '.join(negatives)}." if negatives else ""
        return f"{lead}: {pos}.{neg}"
