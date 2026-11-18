from scoring.profile import profile_as_text, scoring_guide_as_text, cv_summary

# Shared system-prompt preamble for all LLM-backed engines (Ollama, HuggingFace, Claude).
# RulesEngine has no LLM prompt and does not use this module.

_PREAMBLE = """\
You are evaluating job postings for a specific candidate. Score each posting 0-100 based on how well it matches the candidate's preferences and background.

=== CANDIDATE PROFILE ===
{profile}

=== SCORING GUIDE ===
{guide}

=== CANDIDATE BACKGROUND (brief) ===
{cv}

=== SCORING SCALE ===
90-100: Exceptional match across domain, location, seniority, and bonus signals
70-89: Strong match - good fit with minor gaps (e.g. right domain/role but a weaker location)
50-69: Moderate match - worth reviewing, notable gap in 1-2 areas
30-49: Weak match - significant gaps, unlikely to proceed
0-29: Poor match or dealbreaker present\
"""

# Engines without native structured-output support (Ollama, HuggingFace) need the JSON
# shape spelled out in-prompt. Claude's headless engine enforces it via --json-schema
# instead and does not use this constant.
JSON_RESPONSE_INSTRUCTIONS = """\

Respond ONLY with a valid JSON object - no other text, no markdown fences:
{
  "score": <integer 0-100>,
  "summary": "<1-2 sentences explaining the key score drivers>",
  "domain_match": "<brief assessment of domain/technology fit>",
  "location_match": "<brief assessment of location fit vs. the candidate's location preferences>",
  "role_match": "<brief assessment of role type and seniority fit>",
  "bonus_signals": [<strings listing bonus factors found; empty array if none>],
  "dealbreaker": <true or false>,
  "dealbreaker_reason": "<if dealbreaker is true, explain why; else empty string>"
}\
"""


def base_system_prompt() -> str:
    return _PREAMBLE.format(
        profile=profile_as_text(),
        guide=scoring_guide_as_text(),
        cv=cv_summary(),
    )
