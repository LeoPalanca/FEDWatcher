import os
import requests
from bs4 import BeautifulSoup
import mysql.connector
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", "3306")),
    )


def fetch_unprocessed_html_documents(limit=5):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT id, url
        FROM documents
        WHERE raw_text IS NULL
          AND url LIKE %s
        ORDER BY release_date DESC
        LIMIT %s
    """
    cursor.execute(query, ("%.htm%", limit))
    rows = cursor.fetchall()

    cursor.close()
    conn.close()
    return rows


def extract_text_from_html(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    return text


def update_raw_text(document_id, raw_text):
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        UPDATE documents
        SET raw_text = %s
        WHERE id = %s
    """
    cursor.execute(query, (raw_text, document_id))
    conn.commit()

    cursor.close()
    conn.close()


if __name__ == "__main__":
    docs = fetch_unprocessed_html_documents(limit=5)

    if not docs:
        print("No unprocessed HTML documents found.")
    else:
        for doc in docs:
            try:
                print(f"Processing document {doc['id']}: {doc['url']}")
                text = extract_text_from_html(doc["url"])
                update_raw_text(doc["id"], text)
                print(f"Saved raw_text for document {doc['id']} ({len(text)} chars)")
            except Exception as e:
                print(f"Failed document {doc['id']}: {e}")