#!/bin/bash

cd /home/programming/FEDWatcher || exit 1

source venv/bin/activate

mkdir -p logs

echo "===== $(date '+%Y-%m-%d %H:%M:%S') Analyst started =====" >> logs/analyst.log

python agents/analyst.py --db fedwatcher.db --limit 5 >> logs/analyst.log 2>&1

echo "===== $(date '+%Y-%m-%d %H:%M:%S') Analyst finished =====" >> logs/analyst.log
echo "" >> logs/analyst.log

