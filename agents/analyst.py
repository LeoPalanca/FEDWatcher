"""
AnalystAgent – document segmentation and tone scoring.

Splits Fed documents into semantically labelled sections then calls the
Anthropic API to extract a numeric tone_score in [-1, +1] (dovish → hawkish).
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import anthropic

# Model used for tone extraction. Swap to claude-opus-4-7 for higher accuracy
# at greater cost, or claude-haiku-4-5-20251001 for cheap batch back-filling.
_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """\
You are a monetary-policy analyst specialising in Federal Reserve communications.
You read FOMC statements, minutes, and speeches and return a structured JSON object.
Be precise and evidence-based. Never hallucinate numbers or dates.
"""

_USER_TEMPLATE = """\
Analyse the following Federal Reserve {doc_type} for monetary-policy tone.

The document has been segmented into weighted sections:
{sections_block}

Return ONLY a JSON object with these exact keys:
{{
  "tone_score": <float in [-1.0, +1.0]>,
  "overall_tone": <"dovish" | "neutral" | "hawkish">,
  "inflation_assessment": <one sentence>,
  "labor_market_assessment": <one sentence>,
  "forward_guidance": <one sentence>,
  "key_phrases": [<up to 5 verbatim short phrases that drove your score>],
  "confidence": <float in [0.0, 1.0]>
}}

Scoring conventions:
  -1.0  strongly dovish  (rate cuts imminent, significant downside risks)
   0.0  neutral / data-dependent
  +1.0  strongly hawkish (rate hikes imminent, upside inflation risks)

Do not include any text outside the JSON object.
"""

# ---------------------------------------------------------------------------
# Section weights
# Placeholder values – to be validated against Hansen & McMahon (2016) or
# empirically calibrated on historical FOMC data before final submission.
# ---------------------------------------------------------------------------

WEIGHTS: dict[str, dict[str, float]] = {
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
}

# ---------------------------------------------------------------------------
# Sentence-level classifiers
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, re.Pattern] = {
    "forward_guidance": re.compile(
        r"\b("
        r"the committee (?:anticipates|expects|intends|will|judges|seeks|decided|voted)"
        r"|in determining|in assessing"
        r"|stance of policy|policy firming|additional policy"
        r"|rate path|target range"
        r"|remain|continue|gradual|further adjustment"
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

# Titles that appear as section headers in FOMC minutes
_MINUTES_HEADER_RE = re.compile(
    r"^("
    r"Staff Review of (?:the )?Economic"
    r"|Staff Review of (?:the )?Financial"
    r"|Developments in Financial"
    r"|Staff Economic Outlook"
    r"|Open Market Operations"
    r"|Participants’? Views"
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


@dataclass
class ToneResult:
    """Typed container for tone-extraction output, maps to the sentiment table."""

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
            "tone_score": self.tone_score,
            "overall_tone": self.overall_tone,
            "inflation_assessment": self.inflation_assessment,
            "labor_market_assessment": self.labor_market_assessment,
            "forward_guidance": self.forward_guidance,
            "key_phrases": json.dumps(self.key_phrases),
            "confidence": self.confidence,
        }


class AnalystAgent:
    """
    Segments a Fed document then calls the LLM to extract a tone_score in [-1, +1].

    Usage:
        agent = AnalystAgent()
        result = agent.run({"id": 1, "raw_text": "...", "doc_type": "statement"})
        # result is a ToneResult; call result.to_db_row() for the sentiment table.
    """

    WEIGHTS = WEIGHTS

    def __init__(self, api_key: str | None = None) -> None:
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

    # ------------------------------------------------------------------
    # Public interface (AGENTS.md contract)
    # ------------------------------------------------------------------

    def run(self, document: dict[str, Any]) -> ToneResult:
        """
        Analyse a document dict (as stored in the documents table) and return
        a ToneResult ready for insertion into the sentiment table.

        Args:
            document: dict with at least keys: id, raw_text, doc_type
        """
        doc_id: int = document["id"]
        raw_text: str = document.get("raw_text") or ""
        doc_type: str = document.get("doc_type") or "statement"

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

    # ------------------------------------------------------------------
    # LLM integration
    # ------------------------------------------------------------------

    def _call_llm(
        self, sections: dict[str, str], doc_type: str
    ) -> dict[str, Any]:
        weights = WEIGHTS.get(_normalise_doc_type(doc_type), WEIGHTS["statement"])
        sections_block = _format_sections_block(sections, weights)

        prompt = _USER_TEMPLATE.format(
            doc_type=doc_type, sections_block=sections_block
        )

        response = self._client.messages.create(
            model=_MODEL,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        return _parse_llm_json(raw)

    # ------------------------------------------------------------------
    # Segmentation (unchanged public surface)
    # ------------------------------------------------------------------

    def segment_document(self, text: str, doc_type: str) -> dict[str, str]:
        """
        Split *text* into labelled sections.

        Args:
            text:     raw document text
            doc_type: "statement" or "minutes"

        Returns:
            dict mapping section name → concatenated text for that section.
        """
        if _normalise_doc_type(doc_type) == "minutes":
            return self._segment_minutes(text)
        return self._segment_statement(text)

    # ------------------------------------------------------------------
    # Statement segmentation – sentence-level classification
    # ------------------------------------------------------------------

    def _segment_statement(self, text: str) -> dict[str, str]:
        sections = list(WEIGHTS["statement"].keys())
        buckets: dict[str, list[str]] = {s: [] for s in sections}
        for sent in _split_sentences(text):
            buckets[_classify_sentence(sent, sections)].append(sent)
        return {k: " ".join(v) for k, v in buckets.items()}

    # ------------------------------------------------------------------
    # Minutes segmentation – paragraph-level with header detection
    # ------------------------------------------------------------------

    def _segment_minutes(self, text: str) -> dict[str, str]:
        sections = list(WEIGHTS["minutes"].keys())
        buckets: dict[str, list[str]] = {s: [] for s in sections}
        current_section = sections[-1]  # default catch-all

        for para in _split_paragraphs(text):
            first_line = para.split("\n")[0].strip()
            m = _MINUTES_HEADER_RE.match(first_line)
            if m:
                current_section = _map_header(m.group(0), sections)
            else:
                # content-based fallback within current section context
                labels = [_classify_sentence(s, sections) for s in _split_sentences(para)]
                non_fallback = [l for l in labels if l != sections[-1]]
                if non_fallback:
                    current_section = Counter(non_fallback).most_common(1)[0][0]
            buckets[current_section].append(para)

        return {k: " ".join(v) for k, v in buckets.items() if v}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


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
    return "minutes" if "minute" in raw.lower() else "statement"


def _format_sections_block(sections: dict[str, str], weights: dict[str, float]) -> str:
    """Render labelled sections with their weights for the LLM prompt."""
    lines: list[str] = []
    for name, text in sections.items():
        w = weights.get(name, 0.0)
        preview = text[:1500] + "…" if len(text) > 1500 else text
        lines.append(f"[{name} | weight={w:.2f}]\n{preview}")
    return "\n\n".join(lines)


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """
    Extract a JSON object from the LLM reply and validate required keys.
    Raises ValueError if the reply cannot be parsed or is missing tone_score.
    """
    # Strip markdown code fences if the model wrapped the JSON
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()

    try:
        data: dict[str, Any] = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned non-JSON output: {raw!r}") from exc

    if "tone_score" not in data:
        raise ValueError(f"LLM response missing 'tone_score': {data}")

    # Clamp tone_score to [-1, 1] as a safety guard
    data["tone_score"] = max(-1.0, min(1.0, float(data["tone_score"])))
    data.setdefault("confidence", 0.0)
    data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))

    return data
