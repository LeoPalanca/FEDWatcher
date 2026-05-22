#!/bin/bash

cd /home/programming/FEDWatcher || exit 1

source venv/bin/activate

echo "===== $(date '+%Y-%m-%d %H:%M:%S') FakeFed update started ====="
python scripts/update_fakefed_documents.py --db fedwatcher.db
echo "===== $(date '+%Y-%m-%d %H:%M:%S') FakeFed update finished ====="
echo ""
cd ~/FEDWatcher
./scripts/run_fakefed_documents_update.sh >> logs/fakefed_documents_update.log 2>&1
tail -n 50 logs/fakefed_documents_update.log