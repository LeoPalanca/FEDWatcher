#!/bin/bash

cd /home/programming/FEDWatcher || exit 1

source venv/bin/activate

python3 -m agents.monitor_fred --db fedwatcher.db >> logs/fred_backfill.log 2>&1

