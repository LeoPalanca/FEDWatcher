import os
from dotenv import load_dotenv
import mysql.connector

load_dotenv()

conn = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
    port=int(os.getenv("DB_PORT", "3306")),
)

cursor = conn.cursor()

with open("db/schema.sql", "r", encoding="utf-8") as f:
    sql_commands = f.read().split(";")

for command in sql_commands:
    cmd = command.strip()
    if cmd:
        cursor.execute(cmd)

conn.commit()
cursor.close()
conn.close()

print("Database schema initialized successfully.")