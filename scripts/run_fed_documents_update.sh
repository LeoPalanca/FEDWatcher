#!/bin/bash

cd /home/programming/FEDWatcher || exit 1

source venv/bin/activate

mkdir -p logs

echo "===== $(date '+%Y-%m-%d %H:%M:%S') Fed documents update started =====" >> logs/fed_documents_update.log

python3 scripts/update_fed_documents.py >> logs/fed_documents_update.log 2>&1

echo "===== $(date '+%Y-%m-%d %H:%M:%S') Fed documents update finished =====" >> logs/fed_documents_update.log
echo "" >> logs/fed_documents_update.log
