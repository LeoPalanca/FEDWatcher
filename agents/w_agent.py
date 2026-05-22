"""
w_agent.py — weight-aware AnalystAgent.

Reads documents from:
    documents(id, central_bank, doc_type, release_date, url, raw_text, processed_w)

Loads section weights dynamically from:
    weights(doc_type, dimension, weight)

Calls DeepSeek via OpenRouter. Asks for a per-section tone score instead of a
single overall score. Computes the final tone_score as a weighted sum in Python,
so weights can be changed in the database without re-running the LLM.

Writes output to:
    sentiment_w(document_id, forward_guidance_score, inflation_score,
                labor_market_score, general_score, policy_discussion_score,
                tone_score, overall_tone, inflation_assessment,
                labor_market_assessment, forward_guidance, key_phrases, confidence)

Then marks:
    documents.processed_w = 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MODEL = "deepseek/deepseek-v4-flash"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_DB_PATH = "fedwatcher.db"

_DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "statement": {
        "forward_guidance": 0.45,
        "inflation": 0.25,
        "labor_market": 0.15,
        "general": 0.15,
    },
    "minutes": {
        "forward_guidance": 0.40,
        "policy_discussion": 0.25,
        "inflation": 0.20,
        "labor_market": 0.15,
    },
    "speech": {
        "forward_guidance": 0.35,
        "inflation": 0.25,
        "labor_market": 0.20,
        "general": 0.20,
    },
}


_SYSTEM_PROMPT = """\
You are a monetary-policy analyst specialised in Federal Reserve communications.
You read FOMC statements, minutes, and speeches and return a structured JSON object.

Rules:
- Be precise and evidence-based.
- Never hallucinate numbers or dates.
- Only include a label if the statement clearly supports it.
- For each label you select, provide a verbatim quote from the statement as evidence.
- Do not invent or paraphrase quotes.
- Quotes must be copied verbatim from the input text.
- Choose quotes from different sections where possible.
"""

_USER_TEMPLATE = """\
Analyse the following Federal Reserve {doc_type} for monetary-policy tone.

The document has been segmented into the following sections:
{sections_block}

Return ONLY a JSON object with these exact keys:
{{
  "section_scores": {{
{section_scores_format}
  }},
  "overall_tone": <"dovish" | "neutral" | "hawkish">,
  "inflation_assessment": <one sentence on current inflation conditions>,
  "labor_market_assessment": <one sentence on the state of the labor market>,
  "forward_guidance": <one sentence that explicitly signals the future path of rates or policy>,
  "key_phrases": [<up to 5 verbatim short phrases that drove your score>],
  "confidence": <float in [0.0, 1.0]>
}}

Each value in section_scores is a float in [-1.0, +1.0] reflecting the tone of that
specific section only.

Scoring conventions — use the FULL scale, not just the extremes:

  -1.0  strongly dovish   — rate cuts imminent, significant downside risks flagged;
                            language like "will cut", "substantial slack", "well below target".
  -0.5  moderately dovish — easing bias present but not urgent; inflation below target or falling,
                            labor market softening; language like "prepared to adjust downward".
  -0.25 to +0.25  NEUTRAL — assign a score in this range when:
                            • The section is explicitly "data-dependent" with no clear direction.
                            • Risks are described as "balanced".
                            • Language like "patient", "gradual", "assessing incoming data" dominates.
                            Do NOT force a hawkish or dovish score just because inflation is discussed.
  +0.5  moderately hawkish — tightening bias present; inflation above target.
  +1.0  strongly hawkish   — rate hikes imminent, upside inflation risks dominate.

Do not include any text outside the JSON object.
"""


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
    "policy_discussion": re.compile(
        r"\b("
        r"(?:several|many|a few|most|some) (?:participants|members|policymakers)"
        r"|staff projected|the staff|baseline projection"
        r"|balance of risks|uncertainty|risk to the outlook"
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

_MINUTES_HEADER_RE = re.compile(
    r"^("
    r"Staff Review of (?:the )?Economic"
    r"|Staff Review of (?:the )?Financial"
    r"|Developments in Financial"
    r"|Staff Economic Outlook"
    r"|Open Market Operations"
    r"|Participants'? Views"
    r"|Committee Policy"
    r"|Monetary Policy"
    r"|Discussion of (?:the )?Economic"
    r"|Financial Conditions"
    r"|Review of (?:the )?Economic"
    r")",
    re.IGNORECASE,
)

_HEADER_TO_SECTION: list[tuple[str, str]] = [
    ("participants", "forward_guidance"),
    ("committee policy", "forward_guidance"),
    ("monetary policy", "forward_guidance"),
    ("staff economic outlook", "policy_discussion"),
    ("discussion of", "policy_discussion"),
]


# ---------------------------------------------------------------------------
# Data object
# ---------------------------------------------------------------------------

@dataclass
class ToneResult:
    document_id: int
    section_scores: dict[str, float]
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
            "forward_guidance_score": self.section_scores.get("forward_guidance"),
            "inflation_score": self.section_scores.get("inflation"),
            "labor_market_score": self.section_scores.get("labor_market"),
            "general_score": self.section_scores.get("general"),
            "policy_discussion_score": self.section_scores.get("policy_discussion"),
            "tone_score": self.tone_score,
            "overall_tone": self.overall_tone,
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
    Reads document dicts, calls the LLM for per-section scores,
    computes a weighted tone_score from DB weights, returns ToneResult objects.
    """

    def __init__(
        self,
        weights: dict[str, dict[str, float]],
        api_key: str | None = None,
    ) -> None:
        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")

        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is missing. Set it in .env or export it:\n"
                "OPENROUTER_API_KEY='your_key_here'"
            )

        self._client = OpenAI(api_key=api_key, base_url=_OPENROUTER_BASE_URL)
        self._weights = weights

    def run(self, document: dict[str, Any]) -> ToneResult:
        doc_id = int(document["id"])
        raw_text = document.get("raw_text") or ""
        doc_type = document.get("doc_type") or "statement"

        if not raw_text.strip():
            raise ValueError(f"Document {doc_id} has empty raw_text.")

        normalized_type = _normalise_doc_type(doc_type)
        weights = self._weights.get(
            normalized_type,
            _DEFAULT_WEIGHTS.get(normalized_type, _DEFAULT_WEIGHTS["statement"]),
        )

        sections = self._segment_document(raw_text, normalized_type, weights)
        llm_output = self._call_llm(sections, doc_type, weights)

        section_scores = llm_output["section_scores"]
        tone_score = _compute_weighted_score(section_scores, weights)

        return ToneResult(
            document_id=doc_id,
            section_scores=section_scores,
            tone_score=tone_score,
            overall_tone=_score_to_tone(tone_score),
            inflation_assessment=llm_output["inflation_assessment"],
            labor_market_assessment=llm_output["labor_market_assessment"],
            forward_guidance=llm_output["forward_guidance"],
            key_phrases=llm_output.get("key_phrases", []),
            confidence=llm_output.get("confidence", 0.0),
            sections=sections,
        )

    def _call_llm(
        self,
        sections: dict[str, str],
        doc_type: str,
        weights: dict[str, float],
    ) -> dict[str, Any]:
        sections_block = _format_sections_block(sections, weights)
        section_scores_format = ",\n".join(
            f'    "{name}": <float in [-1.0, +1.0]>'
            for name in sections
        )

        prompt = _USER_TEMPLATE.format(
            doc_type=doc_type,
            sections_block=sections_block,
            section_scores_format=section_scores_format,
        )

        response = self._client.chat.completions.create(
            model=_MODEL,
            max_tokens=800,
            temperature=0.0,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )

        raw = response.choices[0].message.content or ""
        return _parse_llm_json(raw, expected_sections=list(sections.keys()))

    def _segment_document(
        self,
        text: str,
        normalized_type: str,
        weights: dict[str, float],
    ) -> dict[str, str]:
        if normalized_type == "minutes":
            return self._segment_minutes(text, weights)
        return self._segment_generic(text, weights)

    def _segment_generic(
        self,
        text: str,
        weights: dict[str, float],
    ) -> dict[str, str]:
        sections = list(weights.keys())
        buckets: dict[str, list[str]] = {s: [] for s in sections}

        for sentence in _split_sentences(text):
            label = _classify_sentence(sentence, sections)
            buckets[label].append(sentence)

        return {k: " ".join(v) for k, v in buckets.items() if v}

    def _segment_minutes(
        self,
        text: str,
        weights: dict[str, float],
    ) -> dict[str, str]:
        sections = list(weights.keys())
        buckets: dict[str, list[str]] = {s: [] for s in sections}
        current_section = sections[-1]

        for paragraph in _split_paragraphs(text):
            first_line = paragraph.split("\n")[0].strip()
            match = _MINUTES_HEADER_RE.match(first_line)

            if match:
                current_section = _map_header(match.group(0), sections)
            else:
                labels = [
                    _classify_sentence(sentence, sections)
                    for sentence in _split_sentences(paragraph)
                ]
                non_fallback = [l for l in labels if l != sections[-1]]
                if non_fallback:
                    current_section = Counter(non_fallback).most_common(1)[0][0]

            buckets[current_section].append(paragraph)

        return {k: " ".join(v) for k, v in buckets.items() if v}


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


def load_weights_from_db(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    rows = conn.execute(
        "SELECT doc_type, dimension, weight FROM weights ORDER BY doc_type, dimension;"
    ).fetchall()

    result: dict[str, dict[str, float]] = {}
    for row in rows:
        dt = row["doc_type"]
        if dt not in result:
            result[dt] = {}
        result[dt][row["dimension"]] = float(row["weight"])

    return result if result else _DEFAULT_WEIGHTS


def ensure_processed_w_column(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(documents);").fetchall()}
    if "processed_w" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN processed_w INTEGER DEFAULT 0;")
        conn.commit()


def validate_schema(conn: sqlite3.Connection) -> None:
    required_tables = {
        "documents": {
            "id", "central_bank", "doc_type", "release_date",
            "url", "raw_text", "processed_w",
        },
        "sentiment_w": {
            "id", "document_id", "forward_guidance_score", "inflation_score",
            "labor_market_score", "general_score", "policy_discussion_score",
            "tone_score", "overall_tone", "inflation_assessment",
            "labor_market_assessment", "forward_guidance", "key_phrases",
            "confidence", "created_at",
        },
    }

    for table_name, required_columns in required_tables.items():
        rows = conn.execute(f"PRAGMA table_info({table_name});").fetchall()
        if not rows:
            raise RuntimeError(f"Missing required table: {table_name}")
        existing = {row["name"] for row in rows}
        missing = required_columns - existing
        if missing:
            raise RuntimeError(
                f"Table {table_name} is missing columns: {', '.join(sorted(missing))}"
            )


def fetch_unprocessed_w_documents(
    conn: sqlite3.Connection,
    limit: int,
    include_speeches: bool = True,
) -> list[dict[str, Any]]:
    doc_types = (
        ("statement", "minutes", "speech") if include_speeches
        else ("statement", "minutes")
    )
    placeholders = ",".join("?" for _ in doc_types)

    rows = conn.execute(
        f"""
        SELECT id, central_bank, doc_type, release_date, url, raw_text, processed_w
        FROM documents
        WHERE COALESCE(processed_w, 0) = 0
          AND raw_text IS NOT NULL
          AND TRIM(raw_text) != ''
          AND LOWER(COALESCE(central_bank, 'FED')) = 'fed'
          AND LOWER(COALESCE(doc_type, 'statement')) IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1 FROM sentiment_w s WHERE s.document_id = documents.id
          )
        ORDER BY release_date ASC, id ASC
        LIMIT ?;
        """,
        (*doc_types, limit),
    ).fetchall()

    return [dict(row) for row in rows]


def insert_sentiment_w(conn: sqlite3.Connection, result: ToneResult) -> None:
    row = result.to_db_row()
    conn.execute(
        """
        INSERT INTO sentiment_w (
            document_id, forward_guidance_score, inflation_score,
            labor_market_score, general_score, policy_discussion_score,
            tone_score, overall_tone, inflation_assessment,
            labor_market_assessment, forward_guidance, key_phrases, confidence
        )
        VALUES (
            :document_id, :forward_guidance_score, :inflation_score,
            :labor_market_score, :general_score, :policy_discussion_score,
            :tone_score, :overall_tone, :inflation_assessment,
            :labor_market_assessment, :forward_guidance, :key_phrases, :confidence
        );
        """,
        row,
    )


def mark_document_processed_w(conn: sqlite3.Connection, document_id: int) -> None:
    conn.execute("UPDATE documents SET processed_w = 1 WHERE id = ?;", (document_id,))


def mark_document_failed_w(conn: sqlite3.Connection, document_id: int) -> None:
    conn.execute("UPDATE documents SET processed_w = 0 WHERE id = ?;", (document_id,))


def process_unprocessed_w_documents(
    db_path: str,
    limit: int,
    dry_run: bool = False,
) -> int:
    conn = connect_db(db_path)
    ensure_processed_w_column(conn)
    validate_schema(conn)

    weights = load_weights_from_db(conn)
    agent = AnalystAgent(weights=weights)
    documents = fetch_unprocessed_w_documents(conn, limit=limit)

    if not documents:
        print("No documents with processed_w = 0 found.")
        conn.close()
        return 0

    print(f"Found {len(documents)} document(s) with processed_w = 0.")

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
                print(f"Section scores: {result.section_scores}")

                if dry_run:
                    print("Dry run: not writing to sentiment_w or updating processed_w.")
                    continue

                insert_sentiment_w(conn, result)
                mark_document_processed_w(conn, doc_id)
                conn.commit()

                processed_count += 1
                print(
                    f"Saved to sentiment_w and marked document {doc_id} "
                    "as processed_w = 1."
                )

            except Exception as exc:
                conn.rollback()
                mark_document_failed_w(conn, doc_id)
                conn.commit()
                print(f"ERROR processing document {doc_id}: {exc}", file=sys.stderr)

    finally:
        conn.close()

    print("-" * 80)
    print(f"Done. Processed {processed_count} document(s) into sentiment_w.")
    return processed_count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized) if s.strip()]


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def _classify_sentence(sentence: str, sections: list[str]) -> str:
    priority = ["forward_guidance", "policy_discussion", "inflation", "labor_market"]
    for label in priority:
        if label in sections and _PATTERNS[label].search(sentence):
            return label
    return sections[-1]


def _map_header(header_text: str, sections: list[str]) -> str:
    lower = header_text.lower()
    for fragment, section in _HEADER_TO_SECTION:
        if fragment in lower and section in sections:
            return section
    return _classify_sentence(header_text, sections)


def _normalise_doc_type(raw: str) -> str:
    value = (raw or "").lower()
    if "minute" in value:
        return "minutes"
    if "speech" in value:
        return "speech"
    return "statement"


def _format_sections_block(sections: dict[str, str], weights: dict[str, float]) -> str:
    lines: list[str] = []
    for name, text in sections.items():
        weight = weights.get(name, 0.0)
        preview = text[:2500] + "..." if len(text) > 2500 else text
        lines.append(f"[{name} | weight={weight:.2f}]\n{preview}")
    return "\n\n".join(lines)


def _compute_weighted_score(
    section_scores: dict[str, float],
    weights: dict[str, float],
) -> float:
    total_weight = sum(weights.get(s, 0.0) for s in section_scores)
    if total_weight == 0.0:
        return 0.0
    score = sum(section_scores[s] * weights.get(s, 0.0) for s in section_scores)
    return max(-1.0, min(1.0, score / total_weight))


def _score_to_tone(score: float) -> str:
    if score <= -0.25:
        return "dovish"
    if score >= 0.25:
        return "hawkish"
    return "neutral"


def _parse_llm_json(raw: str, expected_sections: list[str]) -> dict[str, Any]:
    clean = (raw or "").strip()
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.DOTALL).strip()

    match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
    if match:
        clean = match.group(0)

    try:
        data: dict[str, Any] = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON output: {raw!r}") from exc

    required_keys = {
        "section_scores", "overall_tone", "inflation_assessment",
        "labor_market_assessment", "forward_guidance", "key_phrases", "confidence",
    }
    missing = required_keys - set(data)
    if missing:
        raise ValueError(f"LLM response missing keys: {sorted(missing)}. Response: {data}")

    if not isinstance(data["section_scores"], dict):
        raise ValueError(f"section_scores must be a dict, got: {data['section_scores']!r}")

    sanitized: dict[str, float] = {}
    for section in expected_sections:
        raw_score = data["section_scores"].get(section, 0.0)
        try:
            sanitized[section] = max(-1.0, min(1.0, float(raw_score)))
        except (TypeError, ValueError):
            sanitized[section] = 0.0
    data["section_scores"] = sanitized

    data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))

    tone = str(data["overall_tone"]).lower().strip()
    if tone not in {"dovish", "neutral", "hawkish"}:
        tone = "neutral"
    data["overall_tone"] = tone

    if not isinstance(data["key_phrases"], list):
        data["key_phrases"] = [str(data["key_phrases"])]
    data["key_phrases"] = [
        str(item).strip() for item in data["key_phrases"][:5] if str(item).strip()
    ]

    for key in ["inflation_assessment", "labor_market_assessment", "forward_guidance"]:
        data[key] = str(data.get(key, "")).strip()

    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Weight-aware analyst: stores per-section scores in sentiment_w "
            "so weights can be adjusted without re-running the LLM."
        )
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database.")
    parser.add_argument("--limit", type=int, default=5, help="Max documents to process.")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing to DB.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    process_unprocessed_w_documents(
        db_path=args.db,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
