# E2E test: build-profile skill + daily pipeline (persona `israel_israeli`)

Date: 2026-11-18. Persona: Israel Israeli, fictional junior data scientist (3 years), Tel Aviv.
All per-user files in `examples/israel_israeli/`; every command ran with
`JOBTRACKER_HOME=examples/israel_israeli`. Nothing committed.

## Summary

The skill and the pipeline ran end to end with **0 pipeline errors**. The run surfaced one
recall bug (dedup drops multi-location postings), one skill gap that made the rules engine
over-score off-function Tel Aviv roles, and a Qwen failure mode (score collapse at 35 plus
hallucinated "fully on-site" dealbreakers). On the owner's 49 blind labels, Ollama/Qwen
beat the rules engine (tau-b 0.62 vs 0.47, P@10 0.50 vs 0.20) even though the label pool
came from the rules top 25.

## Decision (owner, 2026-11-18)

Ship the results as they are: fresh skill output, untuned rules, scored by the pipeline,
evaluated against blind human labels. No rerun, resample or relabeling. F1 and F5 are
recorded as known issues:

- **F1 (dedup)**: no effect on this example. All 32 dropped postings are sales, account,
  solutions or security roles; none would change a label or metric.
- **F5 (title exclusions)**: explains the low rules P@10 (0.20). It is part of what the
  example shows: the eval caught a gap in the skill's untuned output. Not tuned after
  labeling, to avoid fitting the rules to the test set.

## Steps run

| # | Step | Result |
|---|---|---|
| 1 | Fictional one-page CV | `examples/israel_israeli/cv.pdf` (HTML rendered with headless Chrome; no installs) |
| 2 | `build-profile` skill | 4 files written; persona answered "all draft defaults" |
| 3 | `scripts/check_profile.py` | Pass: profile, CV, rules, 29 companies; no DB yet, so no score preview |
| 4 | `run_pipeline.sh --dry-run` | 13:33:43 to 15:01:49 (1h28m), exit 0, 0 errors, no email sent |
| + | Blind label sheet | 49 posts: 25 random + 24 from rules top 25 (seed 7), labeled by the owner |
| + | C4 + C5/C6 evals | `evals/results/runs/20261118_150216_c4_israel_israeli/` |

## Numbers (step 5)

**Companies in `companies.yaml`: 29**

| ATS | Companies |
|---|---|
| Greenhouse | 19 |
| Ashby | 8 |
| Comeet | 2 |
| Lever | 0 |

65 candidate companies were probed; 29 verified with open roles, 5 dropped with 0 open roles
(Lightricks, Melio, Wiz, Deel, Explorium), 1 dropped as the wrong company (VAST Data ->
`vast` is Vast, a California space company), 30 not reachable on any supported ATS.

**Postings**

| | Count |
|---|---|
| Fetched | 1,214 (Greenhouse 925, Ashby 267, Comeet 22) |
| Stored | 1,182 (Greenhouse 893, Ashby 267, Comeet 22) |
| Skipped as duplicates | 32 (all Greenhouse; see F1, most are not real duplicates) |
| Marked closed by the closed-posting check | 2 (Comeet's 22 posts are not checkable) |
| Scored by Ollama (`qwen2.5:7b`) | 1,182 of 1,182 (all during the Ingest step; the Ollama upgrade step scored 0) |
| Qwen failures | 0 |
| Scoring at 80 or more | 5 (all exactly 85) |
| Reranked (Claude Haiku) | 5 |
| Pipeline error count | **0** |
| Mean Qwen time | about 4.4 s per post (3 threads sharing Ollama) |

DB: `examples/israel_israeli/jobtracker.db`, 18 MB, 0 user actions, 0 ratings, 0 tags.

## Eval results (C4, C5, C6)

Labels: 49 posts, owner, blind (5: 3, 4: 4, 3: 8, 2: 6, 1: 28). Relevant = 4+.

| System | Kendall tau-b | NDCG@10 | P@10 | Recall@50 |
|---|---|---|---|---|
| Rules engine | 0.47 | 0.49 | 0.20 | 1.00 |
| Ollama (stored score) | 0.62 | 0.77 | 0.50 | 0.86 |
| Pipeline (rerank, then score) | 0.63 | 0.77 | 0.50 | - |
| LLM judge rating (C6, Sonnet) | 0.78 | 0.87 | 0.60 | - |
| Random baseline | 0.00 | 0.21 | 0.14 | - |

- C6: judge vs owner labels, exact 67%, within one grade 96%, weighted kappa 0.79.
- C5: Qwen explanations fully supported 9/20, 20 unsupported claims, reasonable score 6/20.
- Reranker: only 3 labeled posts in the reranked set; too few to read anything into.
- Caveat: the pool is rules-only, so top-of-list metrics should favor rules. Ollama still won.
- Qwen's one recall miss: "Security Research" (Transmit Security), labeled 5, Qwen 35.

## Friction points

### Bugs and recall issues

**F1. Dedup collapses multi-location postings and keeps a random location**
(`ingestion/pipeline.py:36-56`, pipeline Ingest step).
- What happened: the dedup key is company + title + apply URL with the query string
  stripped. JFrog, Riskified, Axonius and Fireblocks use career-page URLs where the job ID is
  only in the query (`?gh_jid=...`), so same-title postings in different locations collapse
  into one. 32 posts dropped. In all 4 cases that had a Tel Aviv copy, the copy kept was abroad
  (e.g. Riskified "Application Security Architect" kept as Lisbon, Tel Aviv dropped).
- Expected: distinct postings (distinct `external_id`) are kept; "never silently drop a post".
- These four were off-function for this persona, but the mechanism hits any persona.

**F2. Qwen score collapse and over-flagged dealbreakers** (`scoring/engines/ollama_engine.py:80-89`,
`scoring/engines/llm_prompt.py:23`).
- What happened: 868 of 1,182 Qwen scores are exactly 35. 655 posts flagged
  `dealbreaker: true`, 317 of them for "fully on-site", usually not stated in the post
  (C5 examples confirm the hallucination). Dealbreaker-flagged scores range 0-75 (19 at 50+),
  while the prompt says dealbreakers belong in 0-29. The engine does not reconcile flag and score.
- Expected: dealbreakers only when the post says so; flag and score consistent.
- Possible trigger: the profile's dealbreaker "fully on-site 5 days a week" (from the skill
  draft). Unverified; a rerun without it would tell.
- Effect: only 5 posts reach 80, so the reranker has almost nothing to order.

### build-profile skill

**F3. Step 2 asks 5 questions "in a single message" via AskUserQuestion, which takes at most 4**
(`.claude/skills/build-profile/SKILL.md`, step 2).
- What happened: I asked all five as one plain-text message with the drafted answer as option A.
- Expected: the skill fits the tool, e.g. merge "dealbreakers" and "companies", or say
  "plain message if more than 4".

**F4. Step 6 keyword tuning cannot run on a first profile** (SKILL.md step 6, `scripts/check_profile.py`).
- What happened: there is no DB yet, so `check_profile.py` prints no preview and the "adjust
  keywords once" check is skipped. Nothing prompts a recheck after the first ingest.
- Expected: the hand-off tells the user to rerun `check_profile.py` after the first ingest, or
  the skill previews against the shipped demo DB.

**F5. Title exclusions only shrink, never grow, so rules over-score adjacent functions**
(SKILL.md step 4, `title_exclusions.soft.phrases`).
- What happened: the skill says to keep the template list and remove the user's own function.
  It never asks to add nearby functions the persona doesn't want. 12 labeled posts scored 84-92
  by rules but labeled 1-2: security engineer, SRE, sales engineer, product manager, FP&A,
  team leads, all in Tel Aviv with "data" or "machine learning" in the description.
  Rules P@10 = 0.20.
- Expected: the skill adds off-target functions next to the target role (for a data scientist:
  security engineer, SRE, sales engineer, product manager...), as the demo persona's file does.
- Later note: a broader, better-supported explanation is in the README ("What the evals say",
  [`evals/mismatch_analysis.py`](../mismatch_analysis.py)): keyword rules can't tell a job's
  function from its description, so local wrong-function roles score high (15 of 22 at 50+,
  vs 0 for Qwen). Title exclusions would catch only part of these.

**F6. Company discovery is slow and leaky** (SKILL.md step 5, `scripts/probe_companies.py`).
- What happened: 30 of 65 candidates were not on Greenhouse, Lever or Ashby, including many
  of the best-known Israeli employers (Wix, Outbrain, Playtika, Hibob, Papaya Global, Tipalti,
  Nayax, Lusha). Retrying with the domain found only 1 more (Gong as `gongio`). The probe
  reports boards with 0 roles as hits (Lightricks, Melio, Wiz, Deel). A first-word guess gave
  the wrong company with 202 roles (`vast`). Generic slugs (`via`, `island`, `unit`) needed a
  manual check of board name and locations. About 60 s per 40 companies.
- Expected: the probe skips or flags 0-role boards and prints the board name and top
  locations so a wrong match is visible.

**F7. Comeet needs hand work** (SKILL.md step 5.2).
- What happened: no helper exists. I grepped careers pages for `comeet.com/jobs/<slug>/<uid>`
  (3 of 12 pages had one), then read the token from that Comeet page and checked it against the
  API. Explorium returned 0 positions. Fiverr and Navina added.
- Expected: a `--comeet <careers URL>` mode in `probe_companies.py` doing exactly this.

### Pipeline

**F8. Qwen scoring runs inside the Ingest step, not the "Ollama upgrade" step**
(`scoring/background.py:139-160`, `scripts/run_pipeline.sh`).
- What happened: ingest starts one non-daemon Ollama thread per source batch (3 here), so
  "Ingest" took 1h27m and the upgrade step scored 0 posts. The `nice -n 19` and
  `OLLAMA_NUM_THREAD=6` throttling in the script only applies to the empty upgrade step; three
  threads hit Ollama at once.
- Expected: step names and timings that match where the work happens; throttling where the
  load runs. No correctness issue: `--upgrade-rules` only picks `engine == "rules"`, so nothing
  is scored twice, and the step works as a retry net.

**F9. Report and log labels** (`scripts/daily_report.py`, `scripts/run_pipeline.sh`).
- The status block calls the step "Llama scoring" while the engine is `ollama/qwen2.5:7b`.
- "Daily report: sending" is shown under `--dry-run`, though nothing is sent.
- Pipeline logs go to `logs/` at the repo root whatever `JOBTRACKER_HOME` is, so personas
  share one log folder.
- The closed-posting check can't check Comeet posts (22 here).

### Evals

**F10. `evals.run` defaults to the latest run folder of any persona and always overwrites the
tracked latest report** (`evals/run.py:173-176`).
- What happened: without `--run-dir`, the persona report would have been written into the demo
  persona's `20261118_130335_c4_sonnet_scorer` folder (editing a past run). I created a new
  folder by hand. The copy to `evals/results/report.md` (tracked) always happens; that file is
  now modified in git with this persona's report.
- Expected: a new run folder by default (or per persona), and no automatic overwrite of a
  tracked file.

**F11. Report wording is demo-specific** (`evals/run.py`, `evals/common.py`).
- "1182 posts in the demo DB" for any persona.
- The C5 footer says C6 checks against "independent (Gemini) reference labels"; here the labels
  are the owner's.
- "Sample:" appears twice (from `LABEL_SOURCES` and from the sheet's sample line).

**F12. Growing a labeled sample is manual** (`evals/label_sheet.py`).
- What happened: going from the random 25 to the pooled 49 meant moving the labeled
  `labels.csv` aside (the script refuses to overwrite it), drawing again, and merging labels by
  `apply_url` by hand. It worked because the random part is identical across modes (verified).
- Expected: a `--keep-labels` option that carries existing labels over by URL.

## Deviations from the written process

- Skill step 2: 5 questions asked as plain text, not via AskUserQuestion (F3).
- Skill step 5: Comeet tokens read with `curl` from careers pages (F7); ambiguous slugs checked
  against the public board APIs.
- Evals: run folder created by hand to avoid writing into a demo run (F10).
- Process note (not the repo): opening 25 tabs with `open -n` started a second Chrome instance,
  which misbehaved; plain `open -a` in batches of 5 worked.

## Files written

- `examples/israel_israeli/`: `cv.pdf`, `cv_structured.json`, `user_profile.yaml`,
  `scoring_rules.yaml`, `companies.yaml`, `jobtracker.db` (18 MB).
- `evals/data/israel_israeli/`: `labels.csv` (49 labeled), `label_sheet.md`,
  `labels_random25.csv` and `label_sheet_random25.md` (the first, random-only sheet).
- `evals/results/runs/20261118_150216_c4_israel_israeli/`: `config.json`, `report.md`,
  `judge_relevance.jsonl`, `judge_faithfulness.jsonl`.
- `evals/results/report.md`: overwritten by `evals.run` (tracked file, now modified).
- `logs/pipeline_20261118_133343.log`.
- This file.

Git: all of the above was committed afterwards, including the 18 MB persona DB and the Comeet
tokens (from public careers pages). This file moved from `ref-docs/` to `evals/results/`. F11 was
fixed after this run; a later run added Claude Haiku and Sonnet as scorers
(`evals/results/runs/20261118_153139_c4_israel_scorers/`).
