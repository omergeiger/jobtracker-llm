"""Build the eval report for one experiment run folder.

    python -m evals.run                                   # latest run folder (or a new one)
    python -m evals.run --run-dir evals/results/runs/<run>

Offline: invariants (C2, C3) and ranking vs human labels (C4). The LLM-judge sections
(C5, C6) report the judge results saved in the run folder by `python -m evals.judge`.
Writes <run-dir>/report.md and copies it to evals/results/report.md (the latest report).
Exit code 1 if an invariant fails.
"""

import argparse
import json
import shutil
import statistics
from datetime import date
from pathlib import Path

from evals.common import (
    LABEL_SOURCE, LABELS_PATH, PERSONA, RESULTS_DIR, REVIEW_THRESHOLD, latest_run_dir, load_labels, load_posts, new_run_dir,
    read_jsonl, record_step,
)
from evals.judge import FAITHFULNESS_FILE, RELEVANCE_FILE
from evals.metrics import (
    RELEVANT, kendall_tau_b, ndcg_at_k, precision_at_k, random_baseline, recall_above, weighted_kappa,
)
from scoring.engines.rules import RulesEngine

K = 10


def _order(scores, labels) -> list[int]:
    """Labels sorted by descending system score (stable for ties)."""
    return [lbl for _, lbl in sorted(zip(scores, labels), key=lambda t: -t[0])]


def invariants(posts: list[dict]) -> tuple[list[str], bool]:
    """C2 (recall: every post scored) and C3 (rerank positions are a clean permutation)."""
    lines = ["## Invariants (verifiable)", ""]

    unscored = [p for p in posts if p["score"] is None or not p["breakdown"] or not p["breakdown"].get("engine")]
    engines: dict[str, int] = {}
    for p in posts:
        e = (p["breakdown"] or {}).get("engine", "none")
        engines[e] = engines.get(e, 0) + 1
    c2 = not unscored
    lines.append(f"- **C2 recall** - every post has a score, breakdown and engine: "
                 f"{'PASS' if c2 else 'FAIL'} ({len(posts) - len(unscored)}/{len(posts)}; "
                 f"engines: {', '.join(f'{k} {v}' for k, v in sorted(engines.items()))})")

    positions = sorted(p["rerank_position"] for p in posts if p["rerank_position"] is not None)
    c3 = positions == list(range(1, len(positions) + 1))
    lines.append(f"- **C3 reranker** - positions form a contiguous 1..N permutation: "
                 f"{'PASS' if c3 else 'FAIL'} (N = {len(positions)})")
    missing_rationale = sum(1 for p in posts if p["rerank_position"] is not None and not p["rerank_rationale"])
    lines.append(f"  - reranked posts without a rationale: {missing_rationale}")
    lines.append("- C1 (extraction + rules engine) and the C2/C3 failure modes run as unit tests: `pytest`")
    return lines + [""], c2 and c3


def ranking_vs_human(posts: list[dict], run_dir: Path) -> list[str]:
    """C4: how well each system orders the human-labeled posts."""
    lines = ["## Ranking vs reference labels (C4)", ""]
    labels = load_labels()
    labeled = [p for p in posts if p["apply_url"] in labels]
    if not labeled:
        return lines + [f"No labels yet. Run `python -m evals.label_sheet`, then fill `{LABELS_PATH.name}`.", ""]

    y = [labels[p["apply_url"]] for p in labeled]
    rules = RulesEngine()
    systems: dict[str, list[float]] = {
        "Rules engine": [rules.score(p["post_dict"]).score for p in labeled],
        "Ollama (stored score)": [p["score"] for p in labeled],
        # Final feed order: reranked posts first by position, then everything else by score
        "Pipeline (rerank, then score)": [
            1000 - p["rerank_position"] if p["rerank_position"] else p["score"] for p in labeled
        ],
    }
    # Claude scorers run on the labeled posts in this run folder (judge --score-with)
    for path in sorted(run_dir.glob("scores_claude-*.jsonl")):
        scored = {r["apply_url"]: r["breakdown"]["score"] for r in read_jsonl(path)}
        if all(p["apply_url"] in scored for p in labeled):
            model = path.stem.removeprefix("scores_claude-")
            systems[f"Claude {model} (scored in this run)"] = [scored[p["apply_url"]] for p in labeled]
    judge = {r["apply_url"]: r["label"] for r in read_jsonl(run_dir / RELEVANCE_FILE)}
    if all(p["apply_url"] in judge for p in labeled):
        systems["LLM judge rating (C6)"] = [judge[p["apply_url"]] for p in labeled]

    dist = ", ".join(f"{v}: {y.count(v)}" for v in (5, 4, 3, 2, 1))
    sheet = LABELS_PATH.with_name("label_sheet.md")
    sample_line = next((l for l in sheet.read_text().splitlines() if l.startswith("Sample: ")), "") if sheet.exists() else ""
    lines += [f"{len(labeled)} posts rated 1-5 for persona `{PERSONA}` ({dist}). Labels: {LABEL_SOURCE}. "
              f"{sample_line + ' ' if sample_line else ''}Relevant = rated {RELEVANT}+. Recall = share of relevant posts scored >= "
              f"{REVIEW_THRESHOLD} (the review threshold). P@{K} is precision within this sample, "
              f"not in the live feed.", "",
              f"| System | Kendall tau-b | NDCG@{K} | P@{K} | Recall@{REVIEW_THRESHOLD} |",
              "|---|---|---|---|---|"]
    for name, scores in systems.items():
        order = _order(scores, y)
        recall = (f"{recall_above(scores, y, REVIEW_THRESHOLD):.2f}"
                  if name.startswith(("Rules", "Ollama", "Claude")) else "-")
        lines.append(f"| {name} | {kendall_tau_b(scores, y):.2f} | {ndcg_at_k(order, K):.2f} | "
                     f"{precision_at_k(order, K):.2f} | {recall} |")
    lines.append(f"| *Random order (baseline)* | 0.00 | "
                 f"{random_baseline(y, lambda l: ndcg_at_k(l, K)):.2f} | "
                 f"{random_baseline(y, lambda l: precision_at_k(l, K)):.2f} | - |")
    lines.append("")

    # Reranker check: within the reranked posts, does the reranker's order beat each system's own order?
    idx = [i for i, p in enumerate(labeled) if p["rerank_position"] is not None]
    if len(idx) >= 3:
        yt = [y[i] for i in idx]
        n = len(idx)
        orders = {"Reranker (Claude listwise)": [-labeled[i]["rerank_position"] for i in idx]}
        orders.update({name: [scores[i] for i in idx] for name, scores in systems.items()
                       if not name.startswith("Pipeline")})
        lines += [f"**Reranker vs score order** on the {n} labeled posts the reranker ordered:", "",
                  f"| Order | Kendall tau-b | NDCG@{n} |", "|---|---|---|"]
        lines += [f"| {name} | {kendall_tau_b(o, yt):.2f} | {ndcg_at_k(_order(o, yt), n):.2f} |"
                  for name, o in orders.items()]
        lines += [f"| *Random order* | 0.00 | {random_baseline(yt, lambda l: ndcg_at_k(l, n)):.2f} |", "",
                  "Scores in this top group are often tied; tau-b accounts for ties, "
                  "NDCG breaks them by input order.", ""]
    return lines


def judge_sections(run_dir: Path) -> list[str]:
    lines = ["## LLM-as-judge (C5, C6)", ""]
    labels = load_labels()
    rel = [r for r in read_jsonl(run_dir / RELEVANCE_FILE) if r["apply_url"] in labels]
    if rel:
        human = [labels[r["apply_url"]] for r in rel]
        judged = [r["label"] for r in rel]
        exact = sum(h == j for h, j in zip(human, judged)) / len(rel)
        within1 = sum(abs(h - j) <= 1 for h, j in zip(human, judged)) / len(rel)
        lines += [f"**C6 judge vs reference labels** - the judge ({rel[0]['judge_model']}) rated the same {len(rel)} posts "
                  f"blind (no scores, no reference labels shown) on the same rubric: exact agreement {exact:.0%}, within one grade {within1:.0%}, "
                  f"quadratic-weighted kappa {weighted_kappa(human, judged):.2f} "
                  f"(1 = perfect, 0 = chance level).", ""]
    else:
        lines += ["C6: not run in this run folder.", ""]

    faith = read_jsonl(run_dir / FAITHFULNESS_FILE)
    if faith:
        by_scorer: dict[str, list[dict]] = {}
        for r in faith:
            by_scorer.setdefault(r["scorer"], []).append(r)
        lines += [f"**C5 explanation faithfulness** - each scorer's breakdown checked against the post text "
                  f"by the judge ({faith[0]['judge_model']}). \"Reasonable\" (score judged >= 4/5) is a "
                  f"secondary signal.", "",
                  "| Scorer | Posts | Fully supported | Unsupported claims | Reasonable score | Mean (1-5) |",
                  "|---|---|---|---|---|---|"]
        for scorer, rows in sorted(by_scorer.items()):
            n = len(rows)
            lines.append(f"| {scorer} | {n} | {sum(r['faithful'] for r in rows)}/{n} | "
                         f"{sum(len(r['unsupported_claims']) for r in rows)} | "
                         f"{sum(r['score_reasonable'] >= 4 for r in rows)}/{n} | "
                         f"{statistics.mean(r['score_reasonable'] for r in rows):.1f} |")
        lines.append("")
        for scorer, rows in sorted(by_scorer.items()):
            claims = [c for r in rows for c in r["unsupported_claims"]]
            if claims:
                lines += [f"Examples of unsupported claims ({scorer}):", ""] + [f"- {c}" for c in claims[:5]] + [""]
        lines += ["*The judge reads the first 3,000 characters of each description. C6 is its check: agreement "
                  "with the reference labels.*", ""]
    else:
        lines += ["C5: not run in this run folder.", ""]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the eval report for one run folder.")
    parser.add_argument("--run-dir", type=Path, help="run folder (default: the latest one)")
    args = parser.parse_args()
    run_dir = (args.run_dir or latest_run_dir() or new_run_dir("report")).resolve()

    posts = load_posts()
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {"steps": []}
    steps = ", ".join(f"{s['step']} ({s['git_commit']})" for s in config["steps"] if s["step"] != "report") or "none"

    inv_lines, ok = invariants(posts)
    report = [f"# Eval report - persona `{PERSONA}` ({date.today().isoformat()})", "",
              f"Run folder: `{run_dir.relative_to(RESULTS_DIR.parent.parent)}` - steps: {steps}. "
              f"{len(posts)} posts in the persona's DB.", ""]
    report += inv_lines + ranking_vs_human(posts, run_dir) + judge_sections(run_dir)

    out = run_dir / "report.md"
    out.write_text("\n".join(report))
    record_step(run_dir, "report", n_posts=len(posts), n_labels=len(load_labels()))
    shutil.copy(out, RESULTS_DIR / "report.md")
    print("\n".join(report))
    print(f"\nWritten to {out} (copied to {RESULTS_DIR / 'report.md'})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
