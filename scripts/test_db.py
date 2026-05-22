import sqlite3
from pathlib import Path

DB_PATH = "fedwatcher.db"

if not Path(DB_PATH).exists():
    raise FileNotFoundError(
        f"Database file not found: {DB_PATH}. Run your init_db.py script first."
    )

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("SELECT sqlite_version();")
print("SQLite version:", cursor.fetchone()[0])

cursor.execute("SELECT 1;")
print("Test query result:", cursor.fetchone())

cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
tables = cursor.fetchall()
print("Tables:", [table[0] for table in tables])

cursor.close()
conn.close()

print("SQLite database connection successful.")