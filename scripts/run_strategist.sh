#!/bin/bash
#
# Cron wrapper for StrategistAgent.
#
# Reads tone history from sentiment_w + macro_data, replays the EWMA chain,
# and persists one row per new document into the signals table.
#
# Suggested crontab entry (every 5 minutes, lined up with w_agent):
#   */5 * * * * /home/programming/FEDWatcher/scripts/run_strategist.sh
#
# flock prevents overlapping runs if the previous run is still going.

LOCKFILE="/tmp/fedwatcher_strategist.lock"
PROJECT_DIR="/home/programming/FEDWatcher"
LOG_FILE="$PROJECT_DIR/logs/strategist.log"

(
    flock -n 200 || exit 0

    cd "$PROJECT_DIR" || exit 1
    source venv/bin/activate

    mkdir -p "$PROJECT_DIR/logs"

    echo "===== $(date '+%Y-%m-%d %H:%M:%S') Strategist started ====="
    python agents/strategist.py --db fedwatcher.db
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') Strategist finished ====="
    echo ""

) 200>"$LOCKFILE" >> "$LOG_FILE" 2>&1
