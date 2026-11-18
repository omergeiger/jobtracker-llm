#!/usr/bin/env bash
# Daily JobTracker pipeline: ingest → rules score → Ollama upgrade → email report
#
# Steps run unconditionally (failures are logged but don't abort the run).
# The Ollama upgrade step is best-effort — skipped gracefully if Ollama is offline.
#
# Usage:
#   ./scripts/run_pipeline.sh
#   ./scripts/run_pipeline.sh --dry-run   # skips email send

set -uo pipefail

# launchd runs with a minimal PATH; the reranker needs the claude CLI from Homebrew
export PATH="/opt/homebrew/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/logs"
RUN_LOG="$LOG_DIR/pipeline_$(date +%Y%m%d_%H%M%S).log"
DRY_RUN=""

for arg in "$@"; do
    [[ "$arg" == "--dry-run" ]] && DRY_RUN="--dry-run"
done

mkdir -p "$LOG_DIR"
source "$PROJECT_ROOT/.venv/bin/activate"
cd "$PROJECT_ROOT"

log() { echo "[$(date +%T)] $*" | tee -a "$RUN_LOG"; }

PIPELINE_ERRORS=()

run_step() {
    local label="$1"; shift
    log "=== $label ==="
    if ! "$@" 2>&1 | tee -a "$RUN_LOG"; then
        log "FAILED: $label"
        PIPELINE_ERRORS+=("$label")
        return 1
    fi
    return 0
}

log "JobTracker daily pipeline starting"
log "Log: $RUN_LOG"

run_step "Ingest" python -m sourcing.run_sourcing --ingest

# Closed-posting check is best-effort — a source hiccup shouldn't abort the run
log "=== Closed-posting check (best-effort) ==="
if ! python "$SCRIPT_DIR/check_closed_postings.py" 2>&1 | tee -a "$RUN_LOG"; then
    log "Closed-posting check skipped (a source may be unreachable)"
fi

run_step "Rules scoring" python -m scoring.service --engine rules

# Ollama upgrade is best-effort — don't add to PIPELINE_ERRORS if it fails
log "=== Ollama upgrade (best-effort) ==="
if ! OLLAMA_NUM_THREAD=6 nice -n 19 python -m scoring.service \
        --engine ollama --upgrade-rules 2>&1 | tee -a "$RUN_LOG"; then
    log "Ollama upgrade skipped (Ollama may be offline)"
fi

# Reranker: "no new posts" exits 0; any failed ranking call exits 1 and counts as a
# pipeline error (it failed silently for weeks when the claude CLI was off PATH)
run_step "Reranker" python -m scoring.reranker

# Runs last: it reads this run's log to put a per-step status block in the email
run_step "Daily report" python "$SCRIPT_DIR/daily_report.py" --run-log "$RUN_LOG" $DRY_RUN

log "=== Pipeline complete — ${#PIPELINE_ERRORS[@]} error(s) ==="
if [[ ${#PIPELINE_ERRORS[@]} -gt 0 ]]; then
    for e in "${PIPELINE_ERRORS[@]}"; do log "  ERROR: $e"; done
    exit 1
fi
