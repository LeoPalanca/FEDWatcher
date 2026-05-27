#!/bin/bash

LOCKFILE="/tmp/fedwatcher_fakefed_update.lock"
PROJECT_DIR="/home/programming/FEDWatcher"
LOG_FILE="$PROJECT_DIR/logs/fakefed_documents_update.log"

(
    flock -n 200 || exit 0

    cd "$PROJECT_DIR" || exit 1
    source venv/bin/activate

    echo "===== $(date '+%Y-%m-%d %H:%M:%S') FakeFed update started ====="
    python -m agents.monitor_fakefed --db fedwatcher.db
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') FakeFed update finished ====="
    echo ""

) 200>"$LOCKFILE" >> "$LOG_FILE" 2>&1
