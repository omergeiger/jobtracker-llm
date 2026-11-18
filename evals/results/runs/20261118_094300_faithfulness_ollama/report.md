# Eval report - demo persona (2026-11-18)

Run folder: `evals/results/runs/20261118_094300_faithfulness_ollama` - steps: judge_faithfulness (29117ea). 630 posts in the demo DB.

## Invariants (verifiable)

- **C2 recall** - every post has a score, breakdown and engine: PASS (630/630; engines: ollama/qwen2.5:7b 630)
- **C3 reranker** - positions form a contiguous 1..N permutation: PASS (N = 25)
  - reranked posts without a rationale: 0
- C1 (extraction + rules engine) and the C2/C3 failure modes run as unit tests: `pytest`

## Ranking vs human judgment (C4)

No labels yet. Run `python -m evals.label_sheet`, then fill `labels.csv`.

## LLM-as-judge (C5, C6)

C6: not run in this run folder.

**C5 explanation faithfulness** - each scorer's breakdown checked against the post text by the judge (sonnet). "Reasonable" (score judged >= 4/5) is a secondary signal.

| Scorer | Posts | Fully supported | Unsupported claims | Reasonable score | Mean (1-5) |
|---|---|---|---|---|---|
| ollama/qwen2.5:7b | 20 | 8/20 | 23 | 7/20 | 2.9 |

Examples of unsupported claims (ollama/qwen2.5:7b):

- The role is described as a 'mid-level position'; the posting never states seniority or level.
- The role is described as a "manager-level account management role"; the posting never states manager-level seniority. It is an Advertiser Account Manager, an individual contributor client-facing role requiring 5-7 years of experience.
- The role involves backend engineering and cloud services (it is a Design Operations role: tooling, procurement, enablement, with no backend engineering or cloud services)
- The focus on backend and distributed systems could be a fit (the posting has no backend/distributed systems focus)
- Tel Aviv position fits tier 1 for remote work (the posting does not say remote; tier 1 is hybrid, and the work mode is not stated)

*The judge reads the first 3,000 characters of each description; C6 is its validation.*
