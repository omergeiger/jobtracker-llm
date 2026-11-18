"""Where do the rules engine and the stored LLM score go wrong? Offline, read-only.

    JOBTRACKER_HOME=examples/israel_israeli python -m evals.mismatch_analysis
    JOBTRACKER_HOME=examples/dana_example python -m evals.mismatch_analysis

Takes the labeled posts rated 1-2 (mismatches for the persona) and splits them by the rules
engine's location verdict: "abroad" (on-site outside the persona's region, or a region the
persona can't work from) vs "local" (location fits, so the mismatch is about the job itself).
For each group, counts how many each system scores at or above the review threshold.
"""

from evals.common import PERSONA, REVIEW_THRESHOLD, load_labels, load_posts
from scoring.engines.rules import RulesEngine

_ABROAD = ("outside", "restricted", "not ac", "Remote - geo")


def main() -> None:
    posts, labels, rules = load_posts(), load_labels(), RulesEngine()
    groups: dict[str, list[tuple[float, float, str]]] = {"local": [], "abroad": []}
    for p in posts:
        if labels.get(p["apply_url"], 9) > 2:
            continue
        r = rules.score(p["post_dict"])
        where = "abroad" if any(k in (r.location_match or "") for k in _ABROAD) else "local"
        groups[where].append((r.score, p["score"], p["title"]))

    print(f"Persona `{PERSONA}`: labeled posts rated 1-2, by location verdict\n")
    print(f"| Mismatch | Posts | Rules >= {REVIEW_THRESHOLD} | LLM >= {REVIEW_THRESHOLD} | Mean rules | Mean LLM |")
    print("|---|---|---|---|---|---|")
    for where, rows in groups.items():
        n = len(rows) or 1
        print(f"| {where} | {len(rows)} | {sum(a >= REVIEW_THRESHOLD for a, _, _ in rows)} | "
              f"{sum(b >= REVIEW_THRESHOLD for _, b, _ in rows)} | {sum(a for a, _, _ in rows) / n:.0f} | "
              f"{sum(b for _, b, _ in rows) / n:.0f} |")
    print("\nLocal mismatches the rules score >= threshold:")
    for a, b, title in sorted(groups["local"], key=lambda x: -x[0]):
        if a >= REVIEW_THRESHOLD:
            print(f"  rules {a:3.0f}  llm {b:3.0f}  {title[:70]}")


if __name__ == "__main__":
    main()
