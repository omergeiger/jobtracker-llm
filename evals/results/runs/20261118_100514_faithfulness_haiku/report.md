# Eval report - demo persona (2026-11-18)

Run folder: `evals/results/runs/20261118_100514_faithfulness_haiku` - steps: score (80d7de4), judge_faithfulness (80d7de4). 630 posts in the demo DB.

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
| claude/haiku | 20 | 6/20 | 22 | 15/20 | 3.9 |

Examples of unsupported claims (claude/haiku):

- Cloudinary is a media/cloud asset platform (the posting says nothing about what Cloudinary does)
- Role is hybrid with 3 office days — the posting only says it is based in the Tokyo office and gives no work mode or office-day count
- The posting appears truncated at "WHAT YOU'LL OWN, 1." — wrong: the text includes items 1 and 2 in full and is cut off later, partway through item 3 ("Problems, found early").
- "Large, established company" is not stated in the posting. It is only loosely implied by 250,000+ customers and an 80+ person design guild.
- Location is described as "hybrid with 3 office days per week" in Paris; the posting only says "Paris" and gives no work mode or office-day information in the visible text.

*The judge reads the first 3,000 characters of each description; C6 is its validation.*
