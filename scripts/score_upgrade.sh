#!/usr/bin/env bash
# Upgrade rules-scored posts to Ollama scoring.
#
# Usage:
#   ./scripts/score_upgrade.sh                        # defaults: 6 threads, 2s delay
#   ./scripts/score_upgrade.sh --delay 5              # longer cooldown
#   ./scripts/score_upgrade.sh --threads 4            # fewer CPU threads
#   ./scripts/score_upgrade.sh --threads 12 --delay 0 # full speed
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Defaults
THREADS=6   # half of 12 physical cores — reduce to lower heat
DELAY_ARGS=()

# Parse our own flags; pass the rest through to the Python service
while [[ $# -gt 0 ]]; do
    case "$1" in
        --threads)
            THREADS="$2"; shift 2 ;;
        *)
            DELAY_ARGS+=("$1"); shift ;;
    esac
done

source "$PROJECT_ROOT/.venv/bin/activate"
cd "$PROJECT_ROOT"

echo "Threads : $THREADS (OLLAMA_NUM_THREAD)"
echo

OLLAMA_NUM_THREAD="$THREADS" nice -n 19 python -m scoring.service --upgrade-rules --verbose "${DELAY_ARGS[@]+"${DELAY_ARGS[@]}"}"
