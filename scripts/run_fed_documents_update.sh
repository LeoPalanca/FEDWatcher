#!/bin/bash

cd /home/programming/FEDWatcher || exit 1

source venv/bin/activate

mkdir -p logs

echo "===== $(date '+%Y-%m-%d %H:%M:%S') Fed documents update started =====" >> logs/fed_documents_update.log

python3 -m agents.monitor_fed --db fedwatcher.db --mode recent >> logs/fed_documents_update.log 2>&1

echo "===== $(date '+%Y-%m-%d %H:%M:%S') Fed documents update finished =====" >> logs/fed_documents_update.log
echo "" >> logs/fed_documents_update.log
