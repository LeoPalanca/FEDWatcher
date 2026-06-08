"""Narrative endpoint: AI-generated copy for the dashboard hero and §02 Breakdown.

This module exposes ``GET /api/narrative`` for the static frontend. It reads
the latest strategist signal and macro context out of SQLite, computes every
deterministic field locally (so an LLM never hallucinates a number), and then
asks ``openai/gpt-oss-120b:free`` (via OpenRouter) for the *prose only* — the
hero headline / body and the breakdown summary headline / paragraphs.

Caching is keyed by the latest signal's id, so the LLM is invoked exactly once
per (re)generation of the strategist's signals table and reused on every page
load until a fresh signal arrives. Falls back to deterministic copy when
``OPENROUTER_API_KEY`` is missing or the LLM call fails — the page never
breaks because narrative is unavailable.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any

from fastapi import APIRouter

from .db import connect, row_to_dict


router = APIRouter()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_MODEL = "deepseek/deepseek-v4-flash"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

BUCKETS: list[str] = ["cut_50", "cut_25", "hold", "hike_25", "hike_50"]
BUCKET_BPS: dict[str, int] = {
    "cut_50": -50, "cut_25": -25, "hold": 0, "hike_25": 25, "hike_50": 50,
}
BUCKET_LABEL: dict[str, str] = {
    "cut_50":  "Cut 50 bps",
    "cut_25":  "Cut 25 bps",
    "hold":    "Hold",
    "hike_25": "Hike 25 bps",
    "hike_50": "Hike 50 bps",
}


_SYSTEM_PROMPT = """\
You are a senior monetary-policy analyst writing concise dashboard copy for
FedWatcher, an agentic Federal Reserve policy nowcast.

About the pipeline that produced this data
------------------------------------------
FedWatcher ingests every new FOMC statement, asks a language model to score
its tone section by section (forward guidance, inflation, labor market,
general assessment), and aggregates them into a single tone score on a
[-1, +1] scale where -1 is strongly dovish, 0 is neutral, and +1 is strongly
hawkish. The tone series is smoothed through time with an exponentially
weighted moving average (EWMA) with a 21-day half-life. The smoothed tone
is then fed alongside the inflation gap (core CPI YoY minus 2%) and the
unemployment gap into an ordered-probit nowcast model. The model outputs a
probability distribution over five mutually exclusive next-meeting outcomes
in basis points: cut_50 (-50 bps), cut_25 (-25 bps), hold (0 bps), hike_25
(+25 bps), hike_50 (+50 bps).

What each field in the user message means
-----------------------------------------
- release_date: date of the FOMC statement being summarised.
- smoothed_tone: EWMA-smoothed tone score in [-1, +1]; -1 strongly dovish,
  0 neutral, +1 strongly hawkish. FedWatcher's headline tone reading.
- tone_implied_next_rate (%): the next-meeting policy rate the tone model
  implies, i.e. current_policy_rate + expected_bps/100.
- market_implied_next_rate (%): the market's read of the next-meeting rate,
  proxied by the 2-year US Treasury yield (DGS2).
- divergence_market_minus_tone_pp: market_implied minus tone_implied in
  percentage points; positive = market more hawkish than tone, negative =
  market more dovish.
- signal_direction: short categorical label describing the divergence.
- market_verdict: "right" when market and tone-implied paths fall on the
  same side of the current effective fed funds rate (ER, latest FEDFUNDS
  observation) — both pricing a hike or both pricing a cut. "wrong" when
  they fall on opposite sides. null when ambiguous.
- probabilities_pct: the full ordered-probit distribution over the five
  buckets, in percent.
- modal_bucket / modal_probability_pct: the highest-probability outcome
  and its mass.
- cumulative_cut_pct / cumulative_hike_pct: total probability on the cut
  side (cut_50 + cut_25) and the hike side (hike_25 + hike_50).
- expected_next_move_bps: probability-weighted expected basis-point move at
  the next meeting.
- distribution_shape: "Right-skewed" (mass leans toward hikes),
  "Left-skewed" (toward cuts), or "Centered".
- distribution_direction: "hawkish", "dovish", or "neutral", derived from
  the sign of expected_next_move_bps.
- latest_macro: most recent monthly snapshot from FRED.
  * core_cpi_yoy_pct: core CPI year-over-year (Fed target = 2%).
  * unemployment_rate: civilian unemployment rate, %.
  * us2y_yield_pct: 2-year Treasury yield, used as the market proxy.
  * policy_rate_pct: effective fed funds rate (FEDFUNDS), the ER anchor
    used by the strategist and by the market verdict.

How to write the copy
---------------------
- The hero headline + body summarise the *overall* state: where tone sits,
  what the modal outcome is, and how that compares to what the market is
  pricing.
- The breakdown headline + paragraphs explain the *shape* of the next-move
  distribution: where mass concentrates, how skewed it is, what would have
  to change to flip the modal outcome.

Style rules
-----------
- Be precise and evidence-based. Never invent numbers.
- Use the figures provided in the user message verbatim; do not round,
  restate, or recompute them.
- No theatre, no hedging filler ("it is important to note"), no questions.
- Plain English; if you use a term of art it must be self-explanatory in
  context.
- Title: at most ten to twelve words.
- Description: one or two short sentences.
- When citing numbers, keep them to two decimals and include units
  (bps, %).
- Always explicitly name the stance — hawkish, dovish, or neutral.

Output STRICT JSON with exactly these keys and no others:
{
  "hero_headline":        <one sentence, 8-15 words, no period required>,
  "hero_body":            <one or two sentences, plain English, no jargon>,
  "breakdown_headline":   <one sentence summarising the distribution shape>,
  "breakdown_paragraphs": [<paragraph 1>, <paragraph 2>]
}

Return ONLY the JSON object — no markdown fences, no commentary.
"""


# Process-wide cache, keyed by (signal_id,). Cleared on uvicorn restart.
_NARRATIVE_CACHE: dict[tuple[int, ...], dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Deterministic helpers (never delegated to the LLM)
# ---------------------------------------------------------------------------

def _classify_tone(score: float | None) -> str:
    if score is None:
        return "Neutral"
    if score >  0.50: return "Strongly hawkish"
    if score >  0.15: return "Moderately hawkish"
    if score > -0.15: return "Neutral"
    if score > -0.50: return "Moderately dovish"
    return "Strongly dovish"


def _format_release_date(date_str: str | None) -> str:
    """Render an ISO date as 'FOMC · May 7 · 2026 · latest read'."""

    if not date_str:
        return "FOMC · latest read"
    try:
        d = date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return f"FOMC · {date_str} · latest read"
    return f"FOMC · {d.strftime('%b %-d').replace(' 0', ' ') if os.name != 'nt' else d.strftime('%b %d').replace(' 0', ' ')} · {d.year} · latest read"


def _compute_breakdown_stats(probs: dict[str, float]) -> dict[str, Any]:
    modal = max(probs, key=lambda b: probs[b])
    cum_cut  = (probs.get("cut_50") or 0) + (probs.get("cut_25") or 0)
    cum_hike = (probs.get("hike_25") or 0) + (probs.get("hike_50") or 0)
    expected_bps = sum(BUCKET_BPS[b] * (probs.get(b) or 0) for b in BUCKETS)
    skew = expected_bps / 25.0
    if expected_bps >  6.25: shape = "Right-skewed"
    elif expected_bps < -6.25: shape = "Left-skewed"
    else: shape = "Centered"
    direction = "hawkish" if expected_bps > 0 else "dovish" if expected_bps < 0 else "neutral"
    return {
        "modal_bucket":    modal,
        "modal_prob":      probs[modal],
        "shape":           shape,
        "expected_bps":    expected_bps,
        "skew":            skew,
        "direction":       direction,
        # Pre-formatted strings the frontend drops straight into the stats grid
        "modal_str":       f"{BUCKET_BPS[modal]:+d} bps · {round(probs[modal] * 100)}%",
        "cum_cut_str":     f"{round(cum_cut * 100)}%",
        "cum_hike_str":    f"{round(cum_hike * 100)}%",
        "skew_str":        f"{skew:+.2f} ({direction})",
    }


def _hero_pills(tone: float | None, stats: dict[str, Any]) -> list[dict[str, str]]:
    tone_label = _classify_tone(tone)
    tone_val = f"tone {tone:+.2f}" if tone is not None else "tone —"
    tone_kind = "hawkish" if (tone or 0) > 0.15 else "dovish" if (tone or 0) < -0.15 else "neutral"
    return [
        {"label": f"{tone_label} · {tone_val}", "kind": tone_kind},
        {"label": f"{BUCKET_LABEL[stats['modal_bucket']]} · {round(stats['modal_prob'] * 100)}% probability",
         "kind": "neutral"},
    ]


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _strip_json_fence(raw: str) -> str:
    """Strip leading/trailing ```json fences if the model added them."""

    clean = (raw or "").strip()
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.DOTALL).strip()
    return clean


def _call_llm(context: dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI  # imported lazily — endpoint still works without openai
    except ImportError:
        return None

    user_prompt = (
        "FedWatcher's strategist has just nowcasted the next FOMC move based on "
        "the latest statement. Write the dashboard hero copy and the §02 "
        "Breakdown summary using ONLY the structured context below — do not "
        "invent figures.\n\n"
        f"Context:\n{json.dumps(context, indent=2, default=str)}"
    )

    try:
        client = OpenAI(api_key=api_key, base_url=_OPENROUTER_BASE_URL)
        response = client.chat.completions.create(
            model=_MODEL,
            temperature=0.4,
            max_tokens=600,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
        )
    except Exception:
        return None

    raw = (response.choices[0].message.content or "") if response and response.choices else ""
    try:
        payload = json.loads(_strip_json_fence(raw))
    except (ValueError, TypeError):
        return None

    required = {"hero_headline", "hero_body", "breakdown_headline", "breakdown_paragraphs"}
    if not required.issubset(payload.keys()):
        return None

    # Normalise paragraphs to a list of strings even if the model returned a string.
    paras = payload["breakdown_paragraphs"]
    if isinstance(paras, str):
        payload["breakdown_paragraphs"] = [p.strip() for p in re.split(r"\n\s*\n+", paras) if p.strip()]

    return payload


# ---------------------------------------------------------------------------
# Deterministic fallback copy (used when the LLM is unavailable)
# ---------------------------------------------------------------------------

def _fallback_copy(
    stats: dict[str, Any],
    tone: float | None,
    direction_word: str,
) -> dict[str, Any]:
    tone_label = _classify_tone(tone).lower()
    modal_label = BUCKET_LABEL[stats["modal_bucket"]]
    modal_pct = round(stats["modal_prob"] * 100)
    return {
        "hero_headline": (
            f"{tone_label.capitalize()} tone, modal outcome is {modal_label.lower()} "
            f"at {modal_pct}%."
        ),
        "hero_body": (
            "FedWatcher reads the Federal Reserve's communication, joins it with "
            "FRED macro series, and projects probabilities over rate-move outcomes. "
            "Numbers, sources, no narrative theatre."
        ),
        "breakdown_headline": (
            f"Mass concentrated on {modal_label.lower()}, with a {direction_word} skew."
            if stats["shape"] != "Centered"
            else f"Probability mass concentrated on {modal_label.lower()}."
        ),
        "breakdown_paragraphs": [
            f"The modal outcome is {modal_label.lower()} at {modal_pct}%; cumulative hike "
            f"probability sits at {stats['cum_hike_str']} versus a cumulative cut probability "
            f"of {stats['cum_cut_str']}.",
            f"Expected next-meeting move is {stats['expected_bps']:+.1f} bps, giving a "
            f"distribution skew of {stats['skew_str']}.",
        ],
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/api/narrative")
def narrative() -> dict[str, Any]:
    """Return AI-generated copy for the hero and §02 Breakdown summary."""

    try:
        conn = connect()
    except FileNotFoundError:
        return _empty_payload()

    with conn:
        signal_row = conn.execute(
            """
            SELECT
                s.id              AS signal_id,
                s.document_id     AS document_id,
                s.smoothed_tone   AS smoothed_tone,
                s.tone_implied_next_rate   AS tone_implied,
                s.market_implied_next_rate AS market_implied,
                s.divergence      AS divergence,
                s.signal_direction AS signal_direction,
                s.market_verdict   AS market_verdict,
                s.prob_cut_50, s.prob_cut_25, s.prob_hold,
                s.prob_hike_25, s.prob_hike_50,
                d.release_date    AS release_date,
                d.doc_type        AS doc_type
            FROM signals s
            JOIN documents d ON d.id = s.document_id
            WHERE s.prob_hold IS NOT NULL
            ORDER BY d.release_date DESC, s.id DESC
            LIMIT 1
            """
        ).fetchone()
        if signal_row is None:
            return _empty_payload()
        signal = row_to_dict(signal_row)

        macro_row = conn.execute(
            """
            SELECT observation_month, core_cpi_yoy, unemployment_rate,
                   us2y_yield, policy_rate
            FROM macro_data
            WHERE policy_rate IS NOT NULL
            ORDER BY observation_month DESC
            LIMIT 1
            """
        ).fetchone()
        macro = row_to_dict(macro_row) if macro_row else {}

    cache_key = (int(signal["signal_id"]),)
    if cache_key in _NARRATIVE_CACHE:
        return _NARRATIVE_CACHE[cache_key]

    probs = {b: float(signal.get(f"prob_{b}") or 0.0) for b in BUCKETS}
    stats = _compute_breakdown_stats(probs)
    tone  = signal.get("smoothed_tone")

    context_for_llm = {
        "release_date":       signal.get("release_date"),
        "smoothed_tone":      tone,
        "tone_implied_next_rate":   signal.get("tone_implied"),
        "market_implied_next_rate": signal.get("market_implied"),
        "divergence_market_minus_tone_pp": signal.get("divergence"),
        "signal_direction":   signal.get("signal_direction"),
        "market_verdict":     signal.get("market_verdict"),
        "probabilities_pct":  {k: round(v * 100, 1) for k, v in probs.items()},
        "modal_bucket":       stats["modal_bucket"],
        "modal_probability_pct": round(stats["modal_prob"] * 100),
        "cumulative_cut_pct":    int(stats["cum_cut_str"].rstrip("%")),
        "cumulative_hike_pct":   int(stats["cum_hike_str"].rstrip("%")),
        "expected_next_move_bps": round(stats["expected_bps"], 1),
        "distribution_shape": stats["shape"],
        "distribution_direction": stats["direction"],
        "latest_macro": {
            "observation_month":  macro.get("observation_month"),
            "core_cpi_yoy_pct":   macro.get("core_cpi_yoy"),
            "unemployment_rate":  macro.get("unemployment_rate"),
            "us2y_yield_pct":     macro.get("us2y_yield"),
            "policy_rate_pct":    macro.get("policy_rate"),
        },
    }

    llm_payload = _call_llm(context_for_llm)
    if llm_payload is None:
        prose = _fallback_copy(stats, tone, stats["direction"])
        source = "fallback"
    else:
        prose = llm_payload
        source = _MODEL

    payload = {
        "hero": {
            "takeaway_line": _format_release_date(signal.get("release_date")),
            "pills":     _hero_pills(tone, stats),
            "headline":  prose["hero_headline"],
            "body":      prose["hero_body"],
        },
        "breakdown": {
            "shape_tag":  stats["shape"],
            "headline":   prose["breakdown_headline"],
            "paragraphs": prose["breakdown_paragraphs"],
            "stats": {
                "modal":           stats["modal_str"],
                "cumulative_cut":  stats["cum_cut_str"],
                "cumulative_hike": stats["cum_hike_str"],
                "skew":            stats["skew_str"],
            },
        },
        "meta": {
            "signal_id":     int(signal["signal_id"]),
            "release_date":  signal.get("release_date"),
            "generated_by":  source,
        },
    }

    _NARRATIVE_CACHE[cache_key] = payload
    return payload


def _empty_payload() -> dict[str, Any]:
    """Returned when there's no signal to summarise yet."""
    return {
        "hero": {"takeaway_line": "FOMC · awaiting first signal", "pills": [],
                 "headline": "", "body": ""},
        "breakdown": {"shape_tag": "—", "headline": "", "paragraphs": [],
                      "stats": {"modal": "—", "cumulative_cut": "—",
                                "cumulative_hike": "—", "skew": "—"}},
        "meta": {"signal_id": None, "release_date": None, "generated_by": None},
    }
