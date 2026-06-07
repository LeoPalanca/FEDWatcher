from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter

from .db import connect, row_to_dict


router = APIRouter()

BUCKETS: list[str] = ["cut_50", "cut_25", "hold", "hike_25", "hike_50"]
BUCKET_BPS: dict[str, int] = {"cut_50": -50, "cut_25": -25, "hold": 0, "hike_25": 25, "hike_50": 50}

# Any statement that follows the previous one by fewer than this many days is
# treated as an intermeeting / emergency action. Regular FOMC cycles are
# 42–56 days; emergencies (Jan 2008, Mar 2020, etc.) fall well below this.
EMERGENCY_GAP_DAYS = 35

# Max distance from a statement to the next non-emergency statement for the
# pair to be used as a (forecast, outcome) anchor. Beyond this we assume a
# meeting is missing from the corpus and decline to score.
MAX_VALIDATION_GAP_DAYS = 90


def _month_offset(ym: str, delta: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    m += delta
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _parse_iso(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def _infer_outcome_at(anchor_ym: str, policy: dict[str, float]) -> str | None:
    """
    Bps move at the meeting in anchor_ym, derived from FEDFUNDS monthly
    averages. Uses the months on either side to skip the meeting month's
    intra-month blending.
    """
    pre = policy.get(_month_offset(anchor_ym, -1))
    post = policy.get(_month_offset(anchor_ym, 1))
    if pre is None or post is None:
        return None
    move_bps = round((post - pre) * 100 / 25) * 25
    move_bps = max(-50, min(50, move_bps))
    return {-50: "cut_50", -25: "cut_25", 0: "hold", 25: "hike_25", 50: "hike_50"}[move_bps]


@router.get("/api/accountability")
def accountability() -> dict[str, Any]:
    """
    Track-record metrics for the strategist's ordered-probit signals.

    Each statement S(M) carries probabilities for the *next* FOMC move, so
    each S(M) is validated against the outcome of the next non-emergency
    statement S(M+1), measured from FEDFUNDS monthly averages.

    Emergency / intermeeting statements (gap < EMERGENCY_GAP_DAYS from the
    prior one) are kept in the table but excluded from scoring — both as
    the forecast and as the anchor — because the strategist forecasts the
    next *scheduled* meeting and cannot fairly be graded on a surprise.
    """
    try:
        conn = connect()
    except FileNotFoundError:
        return {"kpi": {}, "recent": [], "all": []}

    with conn:
        available = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"documents", "signals", "macro_data"}.issubset(available):
            return {"kpi": {}, "recent": [], "all": []}

        policy: dict[str, float] = {
            str(r["observation_month"])[:7]: float(r["policy_rate"])
            for r in conn.execute(
                "SELECT observation_month, policy_rate FROM macro_data"
                " WHERE policy_rate IS NOT NULL ORDER BY observation_month"
            ).fetchall()
        }

        raw = conn.execute(
            """
            SELECT
                d.release_date,
                s.prob_cut_50,
                s.prob_cut_25,
                s.prob_hold,
                s.prob_hike_25,
                s.prob_hike_50
            FROM signals s
            JOIN documents d ON s.document_id = d.id
            WHERE d.doc_type = 'statement'
              AND d.release_date IS NOT NULL
              AND s.prob_hold IS NOT NULL
            ORDER BY d.release_date ASC
            """
        ).fetchall()

    # First pass: parse rows, compute argmax + emergency flag.
    items: list[dict[str, Any]] = []
    prev_dt: date | None = None
    for r in raw:
        rd = row_to_dict(r)
        try:
            dt = _parse_iso(rd["release_date"])
        except ValueError:
            continue

        gap = (dt - prev_dt).days if prev_dt is not None else None
        is_emergency = gap is not None and gap < EMERGENCY_GAP_DAYS

        probs = {b: float(rd.get(f"prob_{b}") or 0.0) for b in BUCKETS}
        modal = max(probs, key=lambda b: probs[b])

        items.append(
            {
                "release_date": rd["release_date"],
                "predicted_bucket": modal,
                "predicted_prob": round(probs[modal], 4),
                "probs": {b: round(probs[b], 4) for b in BUCKETS},
                "is_emergency": is_emergency,
                "actual_bucket": None,
                "hit": None,
                "skip_reason": None,
                "_dt": dt,
            }
        )
        prev_dt = dt

    # Second pass: validate each non-emergency S(i) against the next
    # non-emergency S(j) by reading FEDFUNDS around S(j)'s month.
    for i, s in enumerate(items):
        if s["is_emergency"]:
            s["skip_reason"] = "emergency"
            continue

        nxt = next(
            (items[j] for j in range(i + 1, len(items)) if not items[j]["is_emergency"]),
            None,
        )
        if nxt is None:
            s["skip_reason"] = "no_next_statement"
            continue

        gap_to_next = (nxt["_dt"] - s["_dt"]).days
        if gap_to_next > MAX_VALIDATION_GAP_DAYS:
            s["skip_reason"] = "next_too_far"
            continue

        actual = _infer_outcome_at(nxt["release_date"][:7], policy)
        if actual is None:
            s["skip_reason"] = "no_policy_data"
            continue

        s["actual_bucket"] = actual
        s["hit"] = s["predicted_bucket"] == actual

    meetings = sorted(items, key=lambda x: x["release_date"], reverse=True)
    for m in meetings:
        m.pop("_dt", None)

    scored = [m for m in meetings if m["actual_bucket"] is not None]

    hit_rate: float | None = None
    mae: float | None = None
    brier: float | None = None

    if scored:
        hit_rate = round(sum(1 for m in scored if m["hit"]) / len(scored), 4)

        mae = round(
            sum(
                abs(
                    sum(BUCKET_BPS[b] * m["probs"][b] for b in BUCKETS)
                    - BUCKET_BPS[m["actual_bucket"]]
                )
                for m in scored
            )
            / len(scored),
            1,
        )

        brier = round(
            sum(
                sum(
                    (m["probs"][b] - (1.0 if b == m["actual_bucket"] else 0.0)) ** 2
                    for b in BUCKETS
                )
                for m in scored
            )
            / len(scored),
            4,
        )

    return {
        "kpi": {
            "hit_rate": hit_rate,
            "mae_bps": mae,
            "brier_score": brier,
            "coverage": round(len(scored) / len(meetings), 4) if meetings else 0.0,
            "meetings_total": len(meetings),
            "meetings_scored": len(scored),
            "meetings_emergency": sum(1 for m in meetings if m["is_emergency"]),
        },
        "recent": meetings[:8],
        "all": meetings,
    }
