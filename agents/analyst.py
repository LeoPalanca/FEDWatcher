"""
AnalystAgent – document segmentation layer.

Splits Fed documents into semantically labelled sections with associated
weights before tone scoring (LLM integration added in a later step).
"""

from __future__ import annotations

import re
from collections import Counter

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


class AnalystAgent:
    """
    Splits a Fed document into weighted sections for downstream tone scoring.

    Usage:
        agent = AnalystAgent()
        sections = agent.segment_document(raw_text, doc_type="statement")
        # → {"forward_guidance": "...", "inflation": "...", ...}
    """

    WEIGHTS = WEIGHTS

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
