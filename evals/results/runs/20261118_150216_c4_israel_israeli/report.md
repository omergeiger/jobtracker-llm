# Eval report - persona `israel_israeli` (2026-11-18)

Run folder: `evals/results/runs/20261118_150216_c4_israel_israeli` - steps: judge_relevance (c7f846f), judge_faithfulness (c7f846f). 1182 posts in the demo DB.

## Invariants (verifiable)

- **C2 recall** - every post has a score, breakdown and engine: PASS (1182/1182; engines: ollama/qwen2.5:7b 1182)
- **C3 reranker** - positions form a contiguous 1..N permutation: PASS (N = 5)
  - reranked posts without a rationale: 0
- C1 (extraction + rules engine) and the C2/C3 failure modes run as unit tests: `pytest`

## Ranking vs reference labels (C4)

49 posts rated 1-5 for persona `israel_israeli` (5: 3, 4: 4, 3: 8, 2: 6, 1: 28). Labels: the repo owner, by hand, blind to all scores. Sample: see evals/data/israel_israeli/label_sheet.md (sampling mode recorded there). Sample: 49 posts, mode pooled (25 random, 24 from the pool: rules top 25), seed 7. Relevant = rated 4+. Recall = share of relevant posts scored >= 50 (the review threshold). The sample over-represents both ends of the score range, so P@10 is precision within this sample, not in the live feed.

| System | Kendall tau-b | NDCG@10 | P@10 | Recall@50 |
|---|---|---|---|---|
| Rules engine | 0.47 | 0.49 | 0.20 | 1.00 |
| Ollama (stored score) | 0.62 | 0.77 | 0.50 | 0.86 |
| Pipeline (rerank, then score) | 0.63 | 0.77 | 0.50 | - |
| LLM judge rating (C6) | 0.78 | 0.87 | 0.60 | - |
| *Random order (baseline)* | 0.00 | 0.21 | 0.14 | - |

**Reranker vs score order** on the 3 labeled posts the reranker ordered:

| Order | Kendall tau-b | NDCG@3 |
|---|---|---|
| Reranker (Claude listwise) | 1.00 | 1.00 |
| Rules engine | -1.00 | 0.71 |
| Ollama (stored score) | 0.00 | 1.00 |
| LLM judge rating (C6) | 0.82 | 1.00 |
| *Random order* | 0.00 | 0.85 |

Scores in this top group are often tied; tau-b accounts for ties, NDCG breaks them by input order.

## LLM-as-judge (C5, C6)

**C6 judge vs reference labels** - the judge (sonnet) rated the same 49 posts blind (no scores, no reference labels shown) on the same rubric: exact agreement 67%, within one grade 96%, quadratic-weighted kappa 0.79 (1 = perfect, 0 = chance level).

**C5 explanation faithfulness** - each scorer's breakdown checked against the post text by the judge (sonnet). "Reasonable" (score judged >= 4/5) is a secondary signal.

| Scorer | Posts | Fully supported | Unsupported claims | Reasonable score | Mean (1-5) |
|---|---|---|---|---|---|
| ollama/qwen2.5:7b | 20 | 9/20 | 20 | 6/20 | 2.9 |

Examples of unsupported claims (ollama/qwen2.5:7b):

- The role is a 'senior director position' / over-leveled and management-like: the posting title says 'Director' but explicitly describes an individual contributor role; no seniority level or management duties are stated.
- The role 'requires relocation to London': the posting only lists London as location and doesn't mention relocation requirements.
- The role requires on-site presence for 5 days a week — the posting says the team covers 5 days weekly (shifts), but never mentions on-site/office work; it mentions remote troubleshooting and Slack support.
- Dealbreaker 'fully on-site 5 days a week' is triggered — not supported; the posting does not state work mode as on-site.
- Posting explicitly states on-site presence 'including weekends' — the posting says only that coverage might include one weekend day, not on-site presence.

*The judge reads the first 3,000 characters of each description. C6 is its check: agreement with independent (Gemini) reference labels.*
