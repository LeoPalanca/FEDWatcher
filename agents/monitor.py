import os
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from datetime import datetime
from urllib.parse import urljoin

load_dotenv()

FED_BASE_URL = os.getenv("FED_BASE_URL", "https://www.federalreserve.gov").rstrip("/")
FED_CALENDAR_PATH = os.getenv("FED_CALENDAR_PATH", "/monetarypolicy/fomccalendars.htm")
FED_URL = urljoin(f"{FED_BASE_URL}/", FED_CALENDAR_PATH.lstrip("/"))


def get_db_connection():
    import mysql.connector

    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", "3306")),
    )


def extract_release_date(url: str):
    match = re.search(r"(20\d{6})", url)
    if match:
        date_str = match.group(1)
        try:
            return datetime.strptime(date_str, "%Y%m%d")
        except ValueError:
            return None
    return None


def classify_doc_type(url: str, link_text: str = ""):
    url_lower = url.lower()
    text_lower = link_text.lower()

    if "fomcstatement" in url_lower:
        return "statement"
    if "fomc statement" in text_lower:
        return "statement"
    if "/newsevents/pressreleases/monetary20" in url_lower and "implementation note" not in text_lower:
        return "statement"

    if "minutes" in url_lower or "minutes" in text_lower:
        return "minutes"

    return None


def build_full_url(href: str):
    return urljoin(f"{FED_BASE_URL}/", href)


def fetch_candidate_documents():
    response = requests.get(FED_URL, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = build_full_url(href)
        doc_type = classify_doc_type(full_url, a.get_text(" ", strip=True))

        if doc_type:
            candidates.append({
                "central_bank": "FED",
                "doc_type": doc_type,
                "url": full_url,
                "release_date": extract_release_date(full_url),
                "raw_text": None,
                "processed": False,
            })

    return candidates


def canonical_key(doc):
    date_part = doc["release_date"].strftime("%Y-%m-%d") if doc["release_date"] else "unknown"
    return f'{doc["central_bank"]}_{doc["doc_type"]}_{date_part}'


def is_html(url: str):
    return url.lower().endswith(".htm") or url.lower().endswith(".html")


def deduplicate_documents(documents):
    deduped = {}

    for doc in documents:
        key = canonical_key(doc)

        if key not in deduped:
            deduped[key] = doc
        else:
            existing = deduped[key]

            # Prefer HTML over PDF
            if is_html(doc["url"]) and not is_html(existing["url"]):
                deduped[key] = doc

    return list(deduped.values())


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
    candidates = fetch_candidate_documents()
    clean_docs = deduplicate_documents(candidates)
    inserted = save_documents(clean_docs)

    print(f"Fetched {len(candidates)} candidate documents.")
    print(f"Reduced to {len(clean_docs)} canonical documents.")
    print(f"Inserted {inserted} new documents into database.")
