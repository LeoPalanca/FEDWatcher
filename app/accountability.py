from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .db import connect, row_to_dict


router = APIRouter()

BUCKETS: list[str] = ["cut_50", "cut_25", "hold", "hike_25", "hike_50"]
BUCKET_BPS: dict[str, int] = {"cut_50": -50, "cut_25": -25, "hold": 0, "hike_25": 25, "hike_50": 50}


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


def _infer_outcome(release_date: str, policy: dict[str, float]) -> str | None:
    """
    Derive the actual FOMC move from FEDFUNDS monthly averages.
    Compares the month before the meeting to the month after to avoid
    mid-month blending in the meeting month itself.
    """
    ym = release_date[:7]
    pre = policy.get(_month_offset(ym, -1))
    post = policy.get(_month_offset(ym, 1))
    if pre is None or post is None:
        return None
    move_bps = round((post - pre) * 100 / 25) * 25
    move_bps = max(-50, min(50, move_bps))
    return {-50: "cut_50", -25: "cut_25", 0: "hold", 25: "hike_25", 50: "hike_50"}[move_bps]


@router.get("/api/accountability")
def accountability() -> dict[str, Any]:
    """
    Track-record metrics for every FOMC statement that has a signal row.

    Actual outcome is inferred from macro_data.policy_rate (FEDFUNDS monthly
    average): diff between the month before and the month after the meeting,
    rounded to the nearest 25 bps and clamped to [−50, +50].

    Returns:
      kpi       — hit_rate, mae_bps, brier_score, coverage, meeting counts
      recent    — last 8 meetings (for the predictions table)
      all       — full history
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
            ORDER BY d.release_date DESC
            """
        ).fetchall()

    meetings: list[dict[str, Any]] = []
    for r in raw:
        rd = row_to_dict(r)
        probs = {b: float(rd.get(f"prob_{b}") or 0.0) for b in BUCKETS}
        modal = max(probs, key=lambda b: probs[b])
        actual = _infer_outcome(str(rd["release_date"]), policy)
        meetings.append(
            {
                "release_date": rd["release_date"],
                "predicted_bucket": modal,
                "predicted_prob": round(probs[modal], 4),
                "probs": {b: round(probs[b], 4) for b in BUCKETS},
                "actual_bucket": actual,
                "hit": (modal == actual) if actual is not None else None,
            }
        )

    scored = [m for m in meetings if m["actual_bucket"] is not None]

    hit_rate: float | None = None
    mae: float | None = None
    brier: float | None = None

    if scored:
        hit_rate = round(sum(1 for m in scored if m["hit"]) / len(scored), 4)

        mae = round(
            sum(
                abs(sum(BUCKET_BPS[b] * m["probs"][b] for b in BUCKETS) - BUCKET_BPS[m["actual_bucket"]])
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
        },
        "recent": meetings[:8],
        "all": meetings,
    }
