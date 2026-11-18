---
name: build-profile
description: Create a JobPostTracker search profile from a CV PDF. Reads the CV, asks a few short questions the CV can't answer, and writes cv_structured.json, user_profile.yaml, scoring_rules.yaml and companies.yaml (companies matched to the profile, with verified ATS slugs). Use when the user wants to set up or redo their profile, or says "here is my CV".
argument-hint: <path/to/cv.pdf> [output-dir]
---

# build-profile

Turn a CV PDF into the per-user files JobPostTracker fetches and scores with:

| File | Read by | Content |
|---|---|---|
| `cv_structured.json` | LLM engines (CV summary) | Structured CV |
| `user_profile.yaml` | LLM engines (pasted into the prompt) | Preferences in plain language |
| `scoring_rules.yaml` | RulesEngine | Keyword lists and point values |
| `companies.yaml` | Sourcing | Companies to fetch, per ATS |

## 0. Inputs

- CV PDF path: from the arguments; otherwise ask for it.
- Output dir: second argument if given; else `$JOBTRACKER_HOME` if set; else `resources/`
  (whose per-user files are gitignored). If any target file already exists, say so and confirm
  before overwriting.

## 1. Read the CV, write `cv_structured.json`

Read the PDF with the Read tool. Write JSON with exactly these keys (empty list/string when absent):

```json
{
  "name": "", "headline": "", "contact": {"location": ""},
  "professional_profile": "2-4 sentence summary",
  "experience": [{"title": "", "company": "", "years": "2021-present", "bullets": [""]}],
  "education": [{"degree": "", "institution": "", "years": ""}],
  "skills": [""],
  "projects": [{"name": "", "description": ""}]
}
```

`experience` is most-recent first (the scorer uses `experience[0]` as the current role).
Don't include phone numbers, emails or street addresses: they aren't needed for scoring.

## 2. Draft, then ask (one round)

From the CV, draft: current seniority, likely target roles, domains (primary = strongest recent
work), and home city/country. Then ask the user **in a single message** (use AskUserQuestion
when available, with your drafted answer as the first, recommended option):

1. **Target roles and level** - e.g. "Senior backend engineer, IC track".
2. **Location** - preferred city/area, max office days per week, remote OK?, relocation?
3. **Domains** - what to prioritize, what's fine, anything to avoid.
4. **Dealbreakers and must-haves** - hard no's (e.g. clearance required, on-site 5 days),
   and bonus factors (stack, culture, company stage, compensation floor if they want one).
5. **Companies** - any they definitely want included or excluded, and company size/stage
   preferences. Default option: "Suggest companies that match my profile".

Don't ask anything else. If the user says "just use your draft", proceed with it.

## 3. Write `user_profile.yaml`

Start from `resources/templates/user_profile.yaml`; fill every `<...>` placeholder and delete
optional sections the user didn't care about. Plain language: it is read by an LLM.

## 4. Write `scoring_rules.yaml`

Start from `resources/templates/scoring_rules.yaml` and keep its structure and key names exactly
(the engine reads them). Translate the profile into keywords:

- **domains.primary / open_to**: 8-20 lowercase keywords each, the way job posts phrase them
  (technologies, product areas, synonyms). Keep `primary.points: 35`, `open_to.points: 22`.
- **location.tiers**: one tier per preferred area, keywords = the city plus nearby cities and
  common spellings (job posts write "Tel Aviv-Yafo", "TLV", "Herzliya"...). `home_region`
  keywords = the country. Fill `restricted_regions` with regions the user can't work from.
- **seniority.levels**: give the most points (20) to the target level, fewer the further away.
  All nine levels must stay present.
- **dealbreakers / penalties / bonus**: one entry per item from step 2, keywords as they would
  appear in a posting. Delete placeholder entries that weren't used (an empty `rules: []` or
  `penalties: []` is fine).
- **title_exclusions.soft.phrases**: keep the template list, but remove any phrase that matches
  the user's own target function (e.g. drop "recruiter" for a recruiter).

## 5. Write `companies.yaml`

Only companies listed here are fetched, so this decides what the user gets to see: aim for
breadth (roughly 20-40 companies) over precision; the scorers handle relevance.

1. **Draft a list** from the profile: companies hiring for the target roles in the user's
   location (or remote-friendly ones if they are open to remote), in their primary and
   open-to domains, plus every company they named in step 2. Drop the ones they excluded.
2. **Find each company's ATS and slug:**
   ```bash
   python scripts/probe_companies.py "Company A" "Company B" ...
   ```
   It tries slug spellings on Greenhouse, Lever and Ashby and prints ready-to-paste lines with
   the open-role count. For a miss, retry with the company's domain (`"Gong.io"`) or an
   explicit `--slug`; if a web tool is available, the careers page URL shows the ATS and slug
   (`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`).
   Treat "first-word guess" hits and boards whose roles don't fit the company as wrong.
   Comeet needs `uid:token` from the careers page HTML: add it only if you can read the page.
3. **Show the user one short list** (found, with role counts; not found) and ask once whether
   to add or drop anything. Companies on unsupported ATS platforms can't be fetched: say so.
4. Write `companies.yaml` from `resources/templates/companies.yaml`: one key per source, only
   verified entries.

## 6. Validate and hand off

Run (with `JOBTRACKER_HOME=<output dir>` if the output dir isn't `resources/`):

```bash
python scripts/check_profile.py
```

Fix any error it reports. If a DB with posts exists, look at the preview: if obviously
irrelevant posts score high, or the target role scores low, adjust keywords once and rerun.
Then tell the user what was written and the next commands:

```bash
python -m sourcing.run_sourcing --ingest          # fetch + score
python -m scoring.service --engine rules --rescore # rescore existing posts after edits
streamlit run ui/app.py
```
