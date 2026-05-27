#!/bin/bash

cd /home/programming/FEDWatcher || exit 1

exec 9>/tmp/w_agent.lock
flock -n 9 || { echo "===== $(date '+%Y-%m-%d %H:%M:%S') w_agent already running, skipping =====" >> logs/w_agent.log; exit 1; }

source venv/bin/activate

echo "===== $(date '+%Y-%m-%d %H:%M:%S') w_agent started =====" >> logs/w_agent.log

python3 agents/w_agent.py --db fedwatcher.db --limit 20 >> logs/w_agent.log 2>&1

echo "===== $(date '+%Y-%m-%d %H:%M:%S') w_agent finished =====" >> logs/w_agent.log
echo "" >> logs/w_agent.log
