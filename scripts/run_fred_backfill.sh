#!/bin/bash

cd /home/programming/FEDWatcher || exit 1

source venv/bin/activate

python3 scripts/backfill_fred.py >> logs/fred_backfill.log 2>&1

