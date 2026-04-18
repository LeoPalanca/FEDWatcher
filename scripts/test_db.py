import os
from dotenv import load_dotenv
import mysql.connector

load_dotenv()

db_host = os.getenv("DB_HOST")
db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
db_name = os.getenv("DB_NAME")
db_port = int(os.getenv("DB_PORT", "3306"))

missing = []
for key, value in {
    "DB_HOST": db_host,
    "DB_USER": db_user,
    "DB_PASSWORD": db_password,
    "DB_NAME": db_name,
}.items():
    if not value:
        missing.append(key)

if missing:
    raise ValueError(f"Missing environment variables: {', '.join(missing)}")

conn = mysql.connector.connect(
    host=db_host,
    user=db_user,
    password=db_password,
    database=db_name,
    port=db_port,
)

cursor = conn.cursor()
cursor.execute("SELECT DATABASE();")
print("Current database:", cursor.fetchone()[0])

cursor.execute("SELECT 1;")
print("Test query result:", cursor.fetchone())

cursor.close()
conn.close()

print("Database connection successful.")