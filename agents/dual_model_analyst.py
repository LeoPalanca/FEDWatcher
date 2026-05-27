"""
AnalystAgent - document segmentation, tone scoring, and SQLite persistence.

This version is for the second sentiment pipeline.

Reads documents from:
    documents(id, central_bank, doc_type, release_date, url, raw_text, processed2)

Uses ONLY:
    documents.processed2

Does NOT read or update:
    documents.processed

Writes output ONLY to:
    sentiment2

Then marks:
    documents.processed2 = 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MODELS = [
    "openai/gpt-oss-120b:free",
    "deepseek/deepseek-v4-flash",
]

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_DB_PATH = "fedwatcher.db"


_SYSTEM_PROMPT = """\
You are a monetary-policy analyst specialised in Federal Reserve communications.
You read FOMC statements and speeches and return a structured JSON object.

Rules:
- Be precise and evidence-based.
- Never hallucinate numbers or dates.
- Only include a label if the statement clearly supports it.
- For each label you select, provide a verbatim quote from the statement as evidence.
- Do not invent or paraphrase quotes.
- Quotes must be copied verbatim from the input text.
- Choose quotes from different statements where possible.
"""


_USER_TEMPLATE = """\
Analyse the following Federal Reserve {doc_type} for monetary-policy tone.

The document has been segmented into weighted sections:
{sections_block}

Return ONLY a JSON object with these exact keys:
{{
  "tone_score": <float in [-1.0, +1.0]>,
  "overall_tone": <"dovish" | "neutral" | "hawkish">,
  "inflation_assessment": <one sentence on current inflation conditions>,
  "labor_market_assessment": <one sentence on the state of the labor market>,
  "forward_guidance": <one sentence that explicitly signals the future path of rates or policy>,
  "key_phrases": [<up to 5 verbatim short phrases that drove your score>],
  "confidence": <float in [0.0, 1.0]>
}}

Scoring conventions — use the FULL scale, not just the extremes:

  -1.0  strongly dovish   — rate cuts imminent, significant downside risks flagged;
                            language like "will cut", "substantial slack", "well below target".
  -0.5  moderately dovish — easing bias present but not urgent; inflation below target or falling,
                            labor market softening; language like "prepared to adjust downward".
  -0.25 to +0.25  NEUTRAL — assign a score in this range when:
                            • The statement is explicitly "data-dependent" with no clear direction.
                            • Risks are described as "balanced" across inflation and employment.
                            • The Committee is in a watching/monitoring posture with no rate signal.
                            • Language like "patient", "gradual", "assessing incoming data",
                              "will adjust as appropriate" dominates.
                            • Both upside and downside risks are mentioned without one dominating.
                            Do NOT force a hawkish or dovish score just because the Fed discusses
                            inflation — neutral assessments of inflation are the norm, not the exception.
  +0.5  moderately hawkish — tightening bias present; inflation above target; language like
                             "prepared to tighten further", "upside risks to inflation remain".
  +1.0  strongly hawkish   — rate hikes imminent, upside inflation risks dominate;
                             language like "will hike", "inflation well above target".

Do not include any text outside the JSON object.
"""


# ---------------------------------------------------------------------------
# Section weights
# ---------------------------------------------------------------------------

WEIGHTS: dict[str, dict[str, float]] = {
    "statement": {
        "forward_guidance": 0.45,
        "inflation": 0.25,
        "labor_market": 0.15,
        "general": 0.15,
    },
    "speech": {
        "forward_guidance": 0.35,
        "inflation": 0.25,
        "labor_market": 0.20,
        "general": 0.20,
    },
}


# ---------------------------------------------------------------------------
# Sentence-level classifiers
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, re.Pattern[str]] = {
    "forward_guidance": re.compile(
        r"\b("
        r"the committee (?:anticipates|expects|intends|will|judges|seeks|decided|voted)"
        r"|in determining|in assessing"
        r"|stance of policy|policy firming|additional policy"
        r"|rate path|target range"
        r"|remain|continue|gradual|further adjustment"
        r"|future path|policy path|federal funds rate"
        r")\b",
        re.IGNORECASE,
    ),
    "inflation": re.compile(
        r"\b("
        r"inflation|prices?|price level|price stability|pce|cpi"
        r"|2[\s\-]?percent|deflation|disinflation"
        r")\b",
        re.IGNORECASE,
    ),
    "labor_market": re.compile(
        r"\b("
        r"labor market|unemployment|job gains?|employment|payrolls?"
        r"|wages?|wage growth|workforce|workers?"
        r")\b",
        re.IGNORECASE,
    ),
}


# ---------------------------------------------------------------------------
# Data object
# ---------------------------------------------------------------------------

@dataclass
class ToneResult:
    document_id: int
    tone_score: float
    overall_tone: str
    inflation_assessment: str
    labor_market_assessment: str
    forward_guidance: str
    key_phrases: list[str]
    confidence: float
    sections: dict[str, str] = field(default_factory=dict)

    def to_db_row(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "overall_tone": self.overall_tone,
            "tone_score": self.tone_score,
            "inflation_assessment": self.inflation_assessment,
            "labor_market_assessment": self.labor_market_assessment,
            "forward_guidance": self.forward_guidance,
            "key_phrases": json.dumps(self.key_phrases, ensure_ascii=False),
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Analyst Agent
# ---------------------------------------------------------------------------

class AnalystAgent:
    """
    Reads document dictionaries and returns ToneResult objects.
    Database I/O is handled by helper functions below.
    """

    WEIGHTS = WEIGHTS

    def __init__(self, api_key: str | None = None) -> None:
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")

        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is missing. Set it in .env or export it:\n"
                "OPENROUTER_API_KEY='your_key_here'"
            )

        self._client = OpenAI(
            api_key=api_key,
            base_url=_OPENROUTER_BASE_URL,
        )

    def run(self, document: dict[str, Any]) -> ToneResult:
        doc_id = int(document["id"])
        raw_text = document.get("raw_text") or ""
        doc_type = document.get("doc_type") or "statement"

        if not raw_text.strip():
            raise ValueError(f"Document {doc_id} has empty raw_text.")

        sections = self.segment_document(raw_text, doc_type)
        llm_output = self._call_llm(sections, doc_type)

        return ToneResult(
            document_id=doc_id,
            tone_score=llm_output["tone_score"],
            overall_tone=llm_output["overall_tone"],
            inflation_assessment=llm_output["inflation_assessment"],
            labor_market_assessment=llm_output["labor_market_assessment"],
            forward_guidance=llm_output["forward_guidance"],
            key_phrases=llm_output.get("key_phrases", []),
            confidence=llm_output.get("confidence", 0.0),
            sections=sections,
        )

    def _call_llm(self, sections: dict[str, str], doc_type: str) -> dict[str, Any]:
        normalized_type = _normalise_doc_type(doc_type)
        weights = WEIGHTS.get(normalized_type, WEIGHTS["statement"])
        sections_block = _format_sections_block(sections, weights)

        prompt = _USER_TEMPLATE.format(
            doc_type=doc_type,
            sections_block=sections_block,
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        results: list[dict[str, Any]] = []

        for model in _MODELS:
            response = self._client.chat.completions.create(
                model=model,
                max_tokens=1200,
                temperature=0.0,
                messages=messages,
            )
            raw = response.choices[0].message.content or ""
            results.append(_parse_llm_json(raw))

        return _average_llm_results(results)

    def segment_document(self, text: str, doc_type: str) -> dict[str, str]:
        normalized_type = _normalise_doc_type(doc_type)
        return self._segment_statement_or_speech(text, normalized_type)

    def _segment_statement_or_speech(
        self,
        text: str,
        normalized_type: str,
    ) -> dict[str, str]:
        weights = WEIGHTS.get(normalized_type, WEIGHTS["statement"])
        sections = list(weights.keys())
        buckets: dict[str, list[str]] = {section: [] for section in sections}

        for sentence in _split_sentences(text):
            label = _classify_sentence(sentence, sections)
            buckets[label].append(sentence)

        return {
            key: " ".join(value)
            for key, value in buckets.items()
            if value
        }


# ---------------------------------------------------------------------------
# SQLite integration
# ---------------------------------------------------------------------------

def connect_db(db_path: str) -> sqlite3.Connection:
    db_file = Path(db_path)

    if not db_file.exists():
        raise FileNotFoundError(f"Database not found: {db_file.resolve()}")

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def validate_schema(conn: sqlite3.Connection) -> None:
    required_tables = {
        "documents": {
            "id",
            "central_bank",
            "doc_type",
            "release_date",
            "url",
            "raw_text",
            "processed2",
        },
        "sentiment2": {
            "id",
            "document_id",
            "overall_tone",
            "tone_score",
            "inflation_assessment",
            "labor_market_assessment",
            "forward_guidance",
            "key_phrases",
            "confidence",
            "created_at",
        },
    }

    for table_name, required_columns in required_tables.items():
        rows = conn.execute(f"PRAGMA table_info({table_name});").fetchall()

        if not rows:
            raise RuntimeError(f"Missing required table: {table_name}")

        existing_columns = {row["name"] for row in rows}
        missing_columns = required_columns - existing_columns

        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise RuntimeError(
                f"Table {table_name} is missing columns: {missing}"
            )


def fetch_unprocessed2_documents(
    conn: sqlite3.Connection,
    limit: int,
    include_speeches: bool = True,
) -> list[dict[str, Any]]:
    doc_types = (
        "statement",
        "speech",
    ) if include_speeches else (
        "statement",
    )

    placeholders = ",".join("?" for _ in doc_types)

    rows = conn.execute(
        f"""
        SELECT
            id,
            central_bank,
            doc_type,
            release_date,
            url,
            raw_text,
            processed2
        FROM documents
        WHERE COALESCE(processed2, 0) = 0
          AND raw_text IS NOT NULL
          AND TRIM(raw_text) != ''
          AND LOWER(COALESCE(central_bank, 'FED')) = 'fed'
          AND LOWER(COALESCE(doc_type, 'statement')) IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1
              FROM sentiment2 s
              WHERE s.document_id = documents.id
          )
        ORDER BY release_date ASC, id ASC
        LIMIT ?;
        """,
        (*doc_types, limit),
    ).fetchall()

    return [dict(row) for row in rows]


def insert_sentiment2(conn: sqlite3.Connection, result: ToneResult) -> None:
    row = result.to_db_row()

    conn.execute(
        """
        INSERT INTO sentiment2 (
            document_id,
            overall_tone,
            tone_score,
            inflation_assessment,
            labor_market_assessment,
            forward_guidance,
            key_phrases,
            confidence
        )
        VALUES (
            :document_id,
            :overall_tone,
            :tone_score,
            :inflation_assessment,
            :labor_market_assessment,
            :forward_guidance,
            :key_phrases,
            :confidence
        );
        """,
        row,
    )


def mark_document_processed2(conn: sqlite3.Connection, document_id: int) -> None:
    conn.execute(
        """
        UPDATE documents
        SET processed2 = 1
        WHERE id = ?;
        """,
        (document_id,),
    )


def mark_document_failed2(conn: sqlite3.Connection, document_id: int) -> None:
    """
    Keeps failed documents unprocessed for this second pipeline.
    Does not touch documents.processed.
    """
    conn.execute(
        """
        UPDATE documents
        SET processed2 = 0
        WHERE id = ?;
        """,
        (document_id,),
    )


def process_unprocessed2_documents(
    db_path: str,
    limit: int,
    dry_run: bool = False,
) -> int:
    conn = connect_db(db_path)
    validate_schema(conn)

    agent = AnalystAgent()
    documents = fetch_unprocessed2_documents(conn, limit=limit)

    if not documents:
        print("No documents with processed2 = 0 found.")
        conn.close()
        return 0

    print(f"Found {len(documents)} document(s) with processed2 = 0.")

    processed_count = 0

    try:
        for document in documents:
            doc_id = int(document["id"])
            doc_type = document.get("doc_type")
            release_date = document.get("release_date")
            url = document.get("url")

            print("-" * 80)
            print(
                f"Processing document_id={doc_id} "
                f"type={doc_type} release_date={release_date}"
            )
            print(f"URL: {url}")

            try:
                result = agent.run(document)

                print(
                    f"Result: tone={result.overall_tone}, "
                    f"score={result.tone_score:.3f}, "
                    f"confidence={result.confidence:.3f}"
                )

                if dry_run:
                    print("Dry run enabled: not writing to sentiment2 and not updating processed2.")
                    continue

                insert_sentiment2(conn, result)
                mark_document_processed2(conn, doc_id)
                conn.commit()

                processed_count += 1
                print(
                    f"Saved to sentiment2 and marked document {doc_id} as processed2 = 1."
                )

            except Exception as exc:
                conn.rollback()
                mark_document_failed2(conn, doc_id)
                conn.commit()
                print(
                    f"ERROR processing document {doc_id}: {exc}",
                    file=sys.stderr,
                )

    finally:
        conn.close()

    print("-" * 80)
    print(f"Done. Processed {processed_count} document(s) into sentiment2.")
    return processed_count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()

    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", normalized)
        if sentence.strip()
    ]


def _classify_sentence(sentence: str, sections: list[str]) -> str:
    priority = ["forward_guidance", "inflation", "labor_market"]

    for label in priority:
        if label in sections and _PATTERNS[label].search(sentence):
            return label

    return sections[-1]


def _normalise_doc_type(raw: str) -> str:
    value = (raw or "").lower()

    if "speech" in value:
        return "speech"

    return "statement"


def _format_sections_block(
    sections: dict[str, str],
    weights: dict[str, float],
) -> str:
    lines: list[str] = []

    for name, text in sections.items():
        weight = weights.get(name, 0.0)
        preview = text[:2500] + "..." if len(text) > 2500 else text
        lines.append(f"[{name} | weight={weight:.2f}]\n{preview}")

    return "\n\n".join(lines)


def _average_llm_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        raise ValueError("No LLM results to average.")

    avg_tone = sum(result["tone_score"] for result in results) / len(results)
    avg_confidence = sum(result["confidence"] for result in results) / len(results)

    best = max(results, key=lambda result: result["confidence"])

    score = max(-1.0, min(1.0, avg_tone))

    if score <= -0.25:
        overall_tone = "dovish"
    elif score >= 0.25:
        overall_tone = "hawkish"
    else:
        overall_tone = "neutral"

    seen: set[str] = set()
    key_phrases: list[str] = []

    for result in results:
        for phrase in result.get("key_phrases", []):
            phrase = str(phrase).strip()
            if phrase and phrase not in seen:
                seen.add(phrase)
                key_phrases.append(phrase)

            if len(key_phrases) == 5:
                break

        if len(key_phrases) == 5:
            break

    return {
        "tone_score": score,
        "overall_tone": overall_tone,
        "inflation_assessment": best["inflation_assessment"],
        "labor_market_assessment": best["labor_market_assessment"],
        "forward_guidance": best["forward_guidance"],
        "key_phrases": key_phrases,
        "confidence": max(0.0, min(1.0, avg_confidence)),
    }


def _parse_llm_json(raw: str) -> dict[str, Any]:
    clean = (raw or "").strip()

    clean = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        clean,
        flags=re.DOTALL,
    ).strip()

    match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
    if match:
        clean = match.group(0)

    try:
        data: dict[str, Any] = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON output: {raw!r}") from exc

    required_keys = {
        "tone_score",
        "overall_tone",
        "inflation_assessment",
        "labor_market_assessment",
        "forward_guidance",
        "key_phrases",
        "confidence",
    }

    missing = required_keys - set(data)

    if missing:
        raise ValueError(
            f"LLM response missing keys: {sorted(missing)}. Response: {data}"
        )

    data["tone_score"] = max(-1.0, min(1.0, float(data["tone_score"])))
    data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))

    tone = str(data["overall_tone"]).lower().strip()

    if tone not in {"dovish", "neutral", "hawkish"}:
        score = data["tone_score"]

        if score <= -0.2:
            tone = "dovish"
        elif score >= 0.2:
            tone = "hawkish"
        else:
            tone = "neutral"

    data["overall_tone"] = tone

    if not isinstance(data["key_phrases"], list):
        data["key_phrases"] = [str(data["key_phrases"])]

    data["key_phrases"] = [
        str(item).strip()
        for item in data["key_phrases"][:5]
        if str(item).strip()
    ]

    for key in [
        "inflation_assessment",
        "labor_market_assessment",
        "forward_guidance",
    ]:
        data[key] = str(data.get(key, "")).strip()

    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze documents where processed2 = 0 and write output to sentiment2."
        )
    )

    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database. Default: fedwatcher.db",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of documents to process in this run. Default: 5",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run analysis but do not write to sentiment2 or update processed2.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.limit < 1:
        raise ValueError("--limit must be at least 1")

    process_unprocessed2_documents(
        db_path=args.db,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()