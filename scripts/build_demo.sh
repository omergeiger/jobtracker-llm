#!/usr/bin/env bash
# Rebuild the bundled demo DB (examples/dana_example/jobtracker.db) from public ATS feeds
# (Greenhouse, Lever, Ashby, Comeet), scored against the demo persona in examples/dana_example/.
#
#   ./scripts/build_demo.sh
#
# Steps: fetch + ingest (rules scoring, then Ollama scoring if Ollama is running)
#        -> Ollama catch-up -> Claude listwise rerank (needs the `claude` CLI; best-effort)
# Takes roughly an hour with Ollama (qwen2.5:7b); a few seconds with rules only.
set -uo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"
[[ -x .venv/bin/python ]] && PYTHON=.venv/bin/python
export JOBTRACKER_HOME=examples/dana_example

rm -f "$JOBTRACKER_HOME/jobtracker.db"

echo "=== Fetch + ingest + score ==="
"$PYTHON" -m sourcing.run_sourcing --ingest || exit 1

echo "=== Ollama catch-up (best-effort) ==="
"$PYTHON" -m scoring.service --engine ollama --upgrade-rules \
    || echo "Ollama unavailable: posts keep their rules scores"

echo "=== Rerank top posts with Claude (best-effort) ==="
"$PYTHON" -m scoring.reranker || echo "Reranker failed: the Ranked tab will be empty"

"$PYTHON" - <<'PY'
import sqlite3
con = sqlite3.connect("examples/dana_example/jobtracker.db")
con.execute("DELETE FROM user_actions")
con.commit()
con.execute("VACUUM")
n, scored, ranked = con.execute(
    "select count(*), sum(score is not null), sum(global_rerank_position is not null) from job_posts"
).fetchone()
print(f"Demo DB: {n} posts, {scored} scored, {ranked} reranked")
PY
