import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

database_url = os.getenv("DATABASE_URL")

if not database_url:
    raise ValueError("DATABASE_URL is not set. Add it to your .env file.")

try:
    conn = psycopg2.connect(database_url)
    cur = conn.cursor()
    cur.execute("SELECT 1;")
    result = cur.fetchone()

    print("Database connection successful.")
    print("Test query result:", result)

    cur.close()
    conn.close()

except Exception as e:
    print("Database connection failed.")
    print("Error:", e)