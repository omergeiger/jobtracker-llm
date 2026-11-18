# Eval report - demo persona (2026-11-18)

Run folder: `evals/results/runs/20261118_130335_c4_sonnet_scorer` - steps: judge_relevance (edefc64), score (edefc64). 630 posts in the demo DB.

## Invariants (verifiable)

- **C2 recall** - every post has a score, breakdown and engine: PASS (630/630; engines: ollama/qwen2.5:7b 630)
- **C3 reranker** - positions form a contiguous 1..N permutation: PASS (N = 25)
  - reranked posts without a rationale: 0
- C1 (extraction + rules engine) and the C2/C3 failure modes run as unit tests: `pytest`

## Ranking vs reference labels (C4)

40 posts rated 1-5 for the demo persona (5: 7, 4: 7, 3: 1, 2: 0, 1: 25). Labels: Gemini (via the Gemini UI, a different model family from the scorers and the Claude judge), given the persona profile, the rubric and the full posting text; accepted by the repo owner without edits. Relevant = rated 4+. Recall = share of relevant posts scored >= 50 (the review threshold). The sample over-represents both ends of the score range, so P@10 is precision within this sample, not in the live feed.

| System | Kendall tau-b | NDCG@10 | P@10 | Recall@50 |
|---|---|---|---|---|
| Rules engine | 0.68 | 0.84 | 0.90 | 1.00 |
| Ollama (stored score) | 0.55 | 0.63 | 0.70 | 1.00 |
| Pipeline (rerank, then score) | 0.59 | 0.85 | 0.90 | - |
| Claude sonnet (scored in this run) | 0.68 | 0.85 | 1.00 | 0.86 |
| LLM judge rating (C6) | 0.81 | 0.84 | 1.00 | - |
| *Random order (baseline)* | 0.00 | 0.30 | 0.35 | - |

**Reranker vs score order** on the 16 labeled posts the reranker ordered:

| Order | Kendall tau-b | NDCG@16 |
|---|---|---|
| Reranker (Claude listwise) | 0.49 | 0.92 |
| Rules engine | 0.63 | 0.90 |
| Ollama (stored score) | 0.19 | 0.87 |
| Claude sonnet (scored in this run) | 0.61 | 0.89 |
| LLM judge rating (C6) | 0.64 | 0.88 |
| *Random order* | 0.00 | 0.80 |

Scores in this top group are often tied; tau-b accounts for ties, NDCG breaks them by input order.

## LLM-as-judge (C5, C6)

**C6 judge vs reference labels** - the judge (sonnet) rated the same 40 posts blind (no scores, no reference labels shown) on the same rubric: exact agreement 55%, within one grade 82%, quadratic-weighted kappa 0.77 (1 = perfect, 0 = chance level).

C5: not run in this run folder.
