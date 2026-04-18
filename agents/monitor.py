import os
import requests
from bs4 import BeautifulSoup
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

FED_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", "3306")),
    )


def fetch_fomc_links():
    response = requests.get(FED_URL, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "fomcstatement" in href.lower() or "minutes" in href.lower():
            full_url = href if href.startswith("http") else f"https://www.federalreserve.gov{href}"
            doc_type = "statement" if "fomcstatement" in href.lower() else "minutes"

            links.append({
                "central_bank": "FED",
                "doc_type": doc_type,
                "url": full_url,
                "release_date": None,
                "raw_text": None,
                "processed": False,
            })

    return links


def save_documents(documents):
    conn = get_db_connection()
    cursor = conn.cursor()

    inserted = 0

    for doc in documents:
        sql = """
        INSERT IGNORE INTO documents
        (central_bank, doc_type, release_date, url, raw_text, processed)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        values = (
            doc["central_bank"],
            doc["doc_type"],
            doc["release_date"],
            doc["url"],
            doc["raw_text"],
            doc["processed"],
        )
        cursor.execute(sql, values)
        if cursor.rowcount > 0:
            inserted += 1

    conn.commit()
    cursor.close()
    conn.close()

    return inserted


if __name__ == "__main__":
    docs = fetch_fomc_links()
    inserted = save_documents(docs)
    print(f"Fetched {len(docs)} documents.")
    print(f"Inserted {inserted} new documents into database.")