import sqlite3
import os
from pathlib import Path

# Resolve database path
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "fedwatcher.db"
db_path = os.getenv("FEDWATCHER_DB_PATH") or os.getenv("DATABASE_URL")
if db_path:
    if db_path.startswith("sqlite:///"):
        db_path = db_path.removeprefix("sqlite:///")
    db_path = Path(db_path)
    if not db_path.is_absolute():
        db_path = DEFAULT_DB_PATH.parent / db_path
else:
    db_path = DEFAULT_DB_PATH

print(f"Targeting database: {db_path.resolve()}")

if not db_path.exists():
    print("Database file does not exist. Run init_db.py first.")
    exit(1)

conn = sqlite3.connect(db_path)
conn.execute("PRAGMA foreign_keys = OFF;")
cursor = conn.cursor()

try:
    # 1. Check if signals table has market_snapshot_id column
    cursor.execute("PRAGMA table_info(signals)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "market_snapshot_id" in columns:
        print("Migrating 'signals' table to drop 'market_snapshot_id' and its foreign key constraint...")
        
        # Rename old signals table
        cursor.execute("ALTER TABLE signals RENAME TO signals_old;")
        
        # Create new signals table matching db/schema.sql
        cursor.execute("""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER,
            smoothed_tone REAL,
            tone_implied_next_rate REAL,
            market_implied_next_rate REAL,
            divergence REAL,
            signal_direction TEXT,
            market_verdict TEXT,
            narrative TEXT,
            prob_cut_50 REAL,
            prob_cut_25 REAL,
            prob_hold REAL,
            prob_hike_25 REAL,
            prob_hike_50 REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (document_id) REFERENCES documents(id)
        );
        """)
        
        # Copy data from signals_old to signals (identifying which columns exist in signals_old)
        cursor.execute("PRAGMA table_info(signals_old)")
        old_columns = [row[1] for row in cursor.fetchall()]
        
        # We only copy columns that are present in both tables
        shared_cols = [
            "id", "document_id", "smoothed_tone", "tone_implied_next_rate",
            "market_implied_next_rate", "divergence", "signal_direction",
            "market_verdict", "narrative", "prob_cut_50", "prob_cut_25",
            "prob_hold", "prob_hike_25", "prob_hike_50", "created_at"
        ]
        valid_shared_cols = [col for col in shared_cols if col in old_columns]
        
        cols_str = ", ".join(valid_shared_cols)
        cursor.execute(f"INSERT INTO signals ({cols_str}) SELECT {cols_str} FROM signals_old;")
        print(f"Copied data for columns: {valid_shared_cols}")
        
        # Drop the old table
        cursor.execute("DROP TABLE signals_old;")
        print("Successfully migrated 'signals' table.")
    else:
        print("'signals' table is already migrated (no 'market_snapshot_id' column found).")

    # 2. Check if documents table is missing processed2, processed3, processed_w
    cursor.execute("PRAGMA table_info(documents)")
    doc_cols = [row[1] for row in cursor.fetchall()]
    for col in ["processed2", "processed3", "processed_w"]:
        if col not in doc_cols:
            print(f"Adding column '{col}' to 'documents' table...")
            cursor.execute(f"ALTER TABLE documents ADD COLUMN {col} INTEGER DEFAULT 0;")
            
    # 3. Drop market_data table if it exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='market_data';")
    if cursor.fetchone():
        print("Dropping unused 'market_data' table...")
        cursor.execute("DROP TABLE market_data;")
        
    conn.commit()
    print("Database migration completed successfully.")
except Exception as e:
    conn.rollback()
    print(f"Error during migration: {e}")
    raise
finally:
    cursor.close()
    conn.close()
