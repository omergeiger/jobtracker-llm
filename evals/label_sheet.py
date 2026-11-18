"""Export a blind labeling sheet for the human-judgment eval (C4).

    JOBTRACKER_HOME=examples/<persona> python -m evals.label_sheet --mode pooled
        # writes evals/data/<persona>/labels.csv + label_sheet.md

Three sampling modes; in each, the sample is shuffled and every score is hidden:
- pooled (default): N_RANDOM posts at random from the whole feed, plus N_POOL at random from
  the pool: the union of the systems named in --pool (default: all three):
      rules   top POOL_TOP posts by rules score (recomputed now)
      llm     top POOL_TOP posts by stored score (the LLM score, Qwen in the demo pipeline)
      rerank  the whole reranked set
  The pool makes sure there are enough strong candidates to rank (TREC-style pooling).
  Posts that no pooled system ranks high only enter through the random part, so leaving a
  system out of the pool under-tests the posts only that system would surface.
  e.g. --pool rules: the LLM scorers and the reranker are judged on posts they didn't pick.
- random: N posts at random from the whole feed. The most neutral choice, but at small N it
  holds few strong candidates, so top-of-list metrics (P@10, NDCG@10, reranker table) are weak.
- stratified (how the demo persona's labels were drawn): N_SPREAD posts stratified across
  stored-score buckets plus N_TOP from the reranked set.
Read label_sheet.md, then fill the `label` column of labels.csv (1-5, see the rubric) as the
persona would. Refuses to overwrite a labeled labels.csv.
"""

import argparse
import csv
import random
from pathlib import Path

import config_paths
from evals.common import LABELS_PATH, PERSONA, RUBRIC, load_labels, load_posts
from scoring.engines.rules import RulesEngine
from scoring.profile import profile_as_text

POOL_SYSTEMS = ("rules", "llm", "rerank")
BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]


def write_full_descriptions() -> Path:
    """label_posts_full.md: full descriptions, in labels.csv order (for reading/summarizing aids)."""
    with LABELS_PATH.open() as f:
        rows = list(csv.DictReader(f))
    by_url = {p["apply_url"]: p for p in load_posts()}
    out = LABELS_PATH.with_name("label_posts_full.md")
    lines = ["# Job postings - full descriptions (same numbering as labels.csv / label_sheet.md)", ""]
    for row in rows:
        p = by_url[row["apply_url"]]
        lines += [f"## {row['n']}. {p['title']} - {p['company']}", f"*{p['location']}* - {p['apply_url']}", "",
                  p["description"], ""]
    out.write_text("\n".join(lines))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["pooled", "random", "stratified"], default="pooled")
    parser.add_argument("--n", type=int, default=40, help="random: sample size")
    parser.add_argument("--n-random", type=int, default=25, help="pooled: posts from the whole feed")
    parser.add_argument("--n-pool", type=int, default=15, help="pooled: posts from the pool")
    parser.add_argument("--pool", nargs="+", choices=POOL_SYSTEMS, default=list(POOL_SYSTEMS),
                        help="pooled: systems whose top posts form the pool (default: all)")
    parser.add_argument("--pool-top", type=int, default=20, help="pooled: top-N per score-based system")
    parser.add_argument("--n-spread", type=int, default=30, help="stratified: posts across score buckets")
    parser.add_argument("--n-top", type=int, default=10, help="stratified: posts from the reranked set")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--full-descriptions", action="store_true",
                        help="only write label_posts_full.md for the existing labels.csv")
    args = parser.parse_args()

    if args.full_descriptions:
        print(f"Wrote {write_full_descriptions()}")
        return

    if load_labels():
        raise SystemExit(f"{LABELS_PATH} already has labels; move it aside to resample.")

    rng = random.Random(args.seed)
    posts = sorted((p for p in load_posts() if p["score"] is not None and p["description"]),
                   key=lambda p: p["apply_url"])

    if args.mode == "random":
        sample = rng.sample(posts, min(args.n, len(posts)))
        _write(sample, f"{len(sample)} posts, mode random, seed {args.seed}")
        return

    if args.mode == "pooled":
        sample, n_pool, pool_desc = _pooled(posts, rng, args.n_random, args.n_pool, args.pool, args.pool_top)
        _write(sample, f"{len(sample)} posts, mode pooled ({len(sample) - n_pool} random, "
                       f"{n_pool} from the pool: {pool_desc}), seed {args.seed}")
        return

    reranked = [p for p in posts if p["rerank_position"] is not None]
    top = rng.sample(reranked, min(args.n_top, len(reranked)))
    if len(top) < args.n_top:   # reranker didn't run or ranked few posts: fall back to top scores
        rest = sorted((p for p in posts if p not in top), key=lambda p: -p["score"])
        top += rest[: args.n_top - len(top)]

    pool = [p for p in posts if p not in top]
    per_bucket = args.n_spread // len(BUCKETS)
    spread = []
    for lo, hi in BUCKETS:
        bucket = [p for p in pool if lo <= p["score"] < hi]
        spread += rng.sample(bucket, min(per_bucket, len(bucket)))
    leftover = [p for p in pool if p not in spread]
    spread += rng.sample(leftover, args.n_spread - len(spread))   # top up thin buckets

    sample = top + spread
    rng.shuffle(sample)
    _write(sample, f"{len(sample)} posts, mode stratified ({len(top)} from the top set), seed {args.seed}")


def _pooled(posts: list[dict], rng: random.Random, n_random: int, n_pool: int,
            systems: list[str], pool_top: int) -> tuple[list[dict], int, str]:
    parts: dict[str, list[dict]] = {}
    if "rules" in systems:
        rules = RulesEngine()
        parts[f"rules top {pool_top}"] = sorted(posts, key=lambda p: -rules.score(p["post_dict"]).score)[:pool_top]
    if "llm" in systems:
        parts[f"llm top {pool_top}"] = sorted(posts, key=lambda p: -p["score"])[:pool_top]
    if "rerank" in systems:
        reranked = [p for p in posts if p["rerank_position"] is not None]
        parts[f"rerank set ({len(reranked)})"] = reranked
    pool_desc = " + ".join(parts)
    pool_urls = {p["apply_url"] for part in parts.values() for p in part}

    picked = rng.sample(posts, min(n_random, len(posts)))
    picked_urls = {p["apply_url"] for p in picked}
    pool = [p for p in posts if p["apply_url"] in pool_urls and p["apply_url"] not in picked_urls]
    from_pool = rng.sample(pool, min(n_pool, len(pool)))
    print(f"Pool: {len(pool_urls)} posts ({pool_desc}); "
          f"{sum(p['apply_url'] in pool_urls for p in picked)} of the random picks are in it too")
    sample = picked + from_pool
    rng.shuffle(sample)
    return sample, len(from_pool), pool_desc


def _rel(path: Path) -> Path:
    try:
        return path.relative_to(Path.cwd())
    except ValueError:
        return path


def _write(sample: list[dict], summary: str) -> None:
    LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LABELS_PATH.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "label", "title", "company", "location", "apply_url", "note"])
        for i, p in enumerate(sample, 1):
            w.writerow([i, "", p["title"], p["company"], p["location"], p["apply_url"], ""])

    sheet = LABELS_PATH.with_name("label_sheet.md")
    lines = ["# Labeling sheet (blind: no scores shown)", "", f"Sample: {summary}.", "",
             f"Rate each post in `labels.csv` (column `label`) as persona `{PERSONA}` would "
             f"(profile: `{_rel(config_paths.PROFILE_PATH)}`).", "",
             "## Rubric", "", "```", RUBRIC, "```", "",
             "## Persona profile", "", "```", profile_as_text(), "```", ""]
    for i, p in enumerate(sample, 1):
        desc = " ".join(p["description"].split())[:700]
        lines += [f"## {i}. {p['title']} - {p['company']}", f"*{p['location']}* - {p['apply_url']}", "", desc, ""]
    sheet.write_text("\n".join(lines))
    print(f"Wrote {summary} to {LABELS_PATH} and {sheet}")


if __name__ == "__main__":
    main()
