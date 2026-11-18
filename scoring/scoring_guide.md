# Scoring Guide

How the LLM scorer should interpret the candidate profile (`user_profile.yaml`) when evaluating
a job posting. This guide is generic: every candidate-specific preference lives in the profile.

---

## Signal Tiers

| Profile field | Signal type | Scorer behavior |
|---|---|---|
| `domains.primary` | Strong positive | Significant score boost |
| `domains.open_to` | Mild positive | Modest boost; absence doesn't penalize |
| Domains not listed | Neutral | No effect either way |
| `dealbreakers` | Hard negative | Score very low and set `dealbreaker` to true |
| `soft_negatives` (if present) | Soft negative | Demote, do not reject |
| `bonus_factors` | Soft positive | Each present factor adds a small boost |

The `open_to` list is **unordered**: all entries carry equal weight. If the profile adds notes that
qualify a domain (for example, "only when the role is X, not Y"), apply them.

---

## Location

Location tiers in the profile are **priority-ordered** (tier 1 = best match). Respect any
office-days limit given for a tier. Roles that fit no tier should score low on location, but
should not be auto-rejected unless the profile lists that as a dealbreaker.

---

## Compensation

If the profile has a `compensation` section, flag roles clearly below its floor. Equity, bonuses,
or exceptional scope can offset a base slightly below the floor; use judgment. Most postings
don't state compensation; don't penalize them for that.

---

## Role Type and Seniority

All role types listed in the profile's `role_type.open_to` are equally valid targets. Judge
seniority fit from the title, the stated scope and the candidate's background: penalize roles
that are clearly over- or under-leveled relative to the profile's `seniority`.

---

## CV Reference

The candidate background section summarizes the structured CV. Use it to:
- Assess seniority fit (over- or under-leveled roles)
- Spot relevant experience the posting asks for
- Flag roles that are a clear step backward in scope or impact

Do not penalize a role in `domains.primary` or `domains.open_to` just because the CV doesn't show
that exact sub-domain: the profile states where the candidate wants to go, the CV where they've been.
