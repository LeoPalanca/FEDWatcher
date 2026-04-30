import sqlite3

# Create/connect to SQLite database file
conn = sqlite3.connect("fedwatcher.db")
cursor = conn.cursor()

# Load schema
with open("db/schema.sql", "r", encoding="utf-8") as f:
    sql_script = f.read()

# Execute full script (better than manual split)
cursor.executescript(sql_script)

conn.commit()
cursor.close()
conn.close()

print("SQLite database initialized successfully.")