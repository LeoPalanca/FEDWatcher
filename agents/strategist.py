"""
StrategistAgent – EWMA tone smoothing + ordered-probit policy nowcast.

End-to-end pipeline (matching the model spec in README.md):

    1. Smooth raw tone through irregular time:
           S_t = alpha_t * tone_t + (1 - alpha_t) * S_{t-1}
           alpha_t = 1 - exp(-lambda * Delta t),  lambda = ln(2)/h,  h = 21 days.
    2. Build the latent policy index from smoothed tone and macro features:
           eta_t = beta_S  * S_t
                 + beta_pi * (core_cpi_yoy_t - 2)
                 + beta_u  * (unemployment_t - U_baseline)
    3. Map eta_t to rate-move probabilities via an ordered probit with cut
       points c_1 < ... < c_{K-1}:
           P(Y_t = j_k) = Phi(c_k - eta_t) - Phi(c_{k-1} - eta_t)
       Buckets are next-meeting moves in basis points: -50, -25, 0, +25, +50.
    4. Tone-implied next rate (pct):
           tone_implied_rate_t = current_rate_t + sum_k P(Y = j_k) * j_k / 100.
    5. Divergence vs the market proxy (e.g. DGS2 two-year yield), in
       percentage points. Sign convention: positive means the market is
       pricing a higher next-meeting rate than the tone model.
           divergence_t = market_implied_rate_t - tone_implied_rate_t

Default beta and cut points below are placeholders calibrated to economic
priors. They are sign-coherent (hawkish tone → hike skew, slack → cut
skew, above-target CPI → hike skew) but MUST be refit on historical FOMC
outcomes before being published. NROU/NROUST should replace
UNEMPLOYMENT_BASELINE_PCT once that FRED series is ingested.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable


DEFAULT_DB_PATH = Path("fedwatcher.db")
DEFAULT_SENTIMENT_TABLE = "sentiment_w"
DEFAULT_HALFLIFE_DAYS = 21

# Ordered-probit rate-move buckets, in basis points.
RATE_BUCKETS_BPS: tuple[int, ...] = (-50, -25, 0, 25, 50)

# Placeholder coefficients. To be refit via historical FOMC outcomes.
DEFAULT_BETA: dict[str, float] = {
    "tone_smoothed": 1.50,
    "core_cpi_yoy_gap": 0.40,   # per pp deviation from 2% target
    "unemployment_gap": -0.20,  # per pp deviation from U_baseline
}

# Cut points for the 5-bucket ordered probit on the latent scale.
DEFAULT_CUTS: tuple[float, ...] = (-1.50, -0.50, 0.50, 1.50)

# Fed inflation target and natural-rate proxy used for feature centering.
INFLATION_TARGET_PCT = 2.0
UNEMPLOYMENT_BASELINE_PCT = 4.0  # naive natural-rate proxy until NROU is wired in

# Below this absolute divergence (in pp), tone and market are treated as aligned.
DIVERGENCE_TOLERANCE_PCT = 0.10

_PHI = NormalDist().cdf


@dataclass
class SmoothedTone:
    """One smoothed observation in a tone time series."""

    observation_date: date
    tone_score: float
    smoothed_tone: float
    alpha: float
    days_since_prev: int | None


@dataclass
class PolicySignal:
    """End-to-end strategist output for one document, maps to the signals table."""

    document_id: int | None
    smoothed_tone: float
    probabilities: dict[int, float]            # bucket bps -> probability
    tone_implied_next_rate: float              # in percentage points
    market_implied_next_rate: float | None     # in percentage points
    divergence: float | None                   # market − tone, in pp
    signal_direction: str                      # "Market overestimates" | "Market underestimates" | "aligned"
    narrative: str
    market_verdict: str | None = None          # "right" | "wrong" | None

    def to_db_row(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "smoothed_tone": self.smoothed_tone,
            "tone_implied_next_rate": self.tone_implied_next_rate,
            "market_implied_next_rate": self.market_implied_next_rate,
            "divergence": self.divergence,
            "signal_direction": self.signal_direction,
            "market_verdict": self.market_verdict,
            "narrative": self.narrative,
        }


class StrategistAgent:
    """
    Time-aware EWMA smoothing + ordered-probit nowcast of the next FOMC move.

    Default beta and cut points are placeholders; they should be refit on
    historical labelled data before publishing results.

    Usage:
        agent = StrategistAgent()

        # Backfill / batch smoothing
        series = agent.smooth_tones(sentiment_rows)

        # One signal per new document
        signal = agent.run(
            sentiment={"document_id": 42,
                       "tone_score": 0.30,
                       "release_date": "2026-03-19"},
            macro={"core_cpi_yoy": 3.1, "unemployment_rate": 4.0},
            market={"current_rate": 5.25, "us2y_yield": 4.60},
            prior_smoothed_tone=series[-1].smoothed_tone,
            prior_release_date=series[-1].observation_date,
        )
    """

    DEFAULT_HALFLIFE_DAYS = DEFAULT_HALFLIFE_DAYS
    RATE_BUCKETS_BPS = RATE_BUCKETS_BPS
    DEFAULT_BETA = DEFAULT_BETA
    DEFAULT_CUTS = DEFAULT_CUTS

    def __init__(
        self,
        halflife_days: float = DEFAULT_HALFLIFE_DAYS,
        beta: dict[str, float] | None = None,
        cuts: tuple[float, ...] | None = None,
        buckets_bps: tuple[int, ...] = RATE_BUCKETS_BPS,
    ) -> None:
        self.halflife_days = halflife_days
        self._lambda = math.log(2) / halflife_days
        self.beta = dict(beta) if beta else dict(DEFAULT_BETA)
        self.cuts = tuple(cuts) if cuts else DEFAULT_CUTS
        self.buckets_bps = tuple(buckets_bps)

        if len(self.cuts) + 1 != len(self.buckets_bps):
            raise ValueError(
                f"need {len(self.buckets_bps) - 1} cut points for "
                f"{len(self.buckets_bps)} buckets, got {len(self.cuts)}"
            )

    # ------------------------------------------------------------------
    # Public interface (AGENTS.md contract)
    # ------------------------------------------------------------------

    def run(
        self,
        sentiment: dict[str, Any],
        macro: dict[str, Any],
        market: dict[str, Any],
        prior_smoothed_tone: float | None = None,
        prior_release_date: str | date | datetime | None = None,
    ) -> PolicySignal:
        """
        Compose one EWMA smoothing step + ordered-probit nowcast + divergence.

        Args:
            sentiment: dict with 'tone_score' and 'release_date'. 'document_id'
                       is forwarded to the result if present.
            macro:     dict with 'core_cpi_yoy' (pct) and 'unemployment_rate' (pct).
            market:    dict with 'current_rate' (pct, policy midpoint) and
                       optional 'us2y_yield' (pct) as the market proxy.
            prior_smoothed_tone: most recent S_{t-1}, or None to seed.
            prior_release_date:  date associated with prior_smoothed_tone,
                       required when prior_smoothed_tone is provided.
        """
        smoothed = self._smooth_step(
            release_date=sentiment["release_date"],
            tone_score=float(sentiment["tone_score"]),
            prior_smoothed=prior_smoothed_tone,
            prior_date=prior_release_date,
        )

        probs = self.predict_probabilities(
            smoothed_tone=smoothed.smoothed_tone,
            core_cpi_yoy=float(macro["core_cpi_yoy"]),
            unemployment_rate=float(macro["unemployment_rate"]),
        )

        current_rate = float(market["current_rate"])
        tone_implied = self.tone_implied_rate(probs, current_rate)

        market_proxy = market.get("us2y_yield")
        market_implied = float(market_proxy) if market_proxy is not None else None
        divergence, direction = self._divergence(tone_implied, market_implied)
        verdict = self._market_verdict(tone_implied, market_implied, current_rate)

        return PolicySignal(
            document_id=sentiment.get("document_id"),
            smoothed_tone=smoothed.smoothed_tone,
            probabilities=probs,
            tone_implied_next_rate=tone_implied,
            market_implied_next_rate=market_implied,
            divergence=divergence,
            signal_direction=direction,
            narrative=self._narrative(probs, divergence, direction, verdict),
            market_verdict=verdict,
        )

    # ------------------------------------------------------------------
    # EWMA tone smoothing
    # ------------------------------------------------------------------

    def smooth_tones(
        self, observations: Iterable[dict[str, Any]]
    ) -> list[SmoothedTone]:
        """
        Smooth a chronologically irregular tone series in one batch.

        Args:
            observations: iterable of dicts with keys 'release_date' and
                'tone_score'. release_date may be a date, a datetime, or a
                'YYYY-MM-DD' string. Order does not matter; observations
                are sorted by date internally.

        Returns:
            List of SmoothedTone sorted by observation_date. The first
            observation seeds the series with alpha = 1.0 and
            smoothed_tone = tone_score.
        """
        parsed = sorted(
            (_parse_date(o["release_date"]), float(o["tone_score"]))
            for o in observations
        )

        series: list[SmoothedTone] = []
        prev_date: date | None = None
        smoothed: float = 0.0

        for obs_date, raw_tone in parsed:
            if prev_date is None:
                alpha = 1.0
                smoothed = raw_tone
                days_since_prev: int | None = None
            else:
                days_since_prev = (obs_date - prev_date).days
                alpha = self._alpha(days_since_prev)
                smoothed = alpha * raw_tone + (1 - alpha) * smoothed

            series.append(
                SmoothedTone(
                    observation_date=obs_date,
                    tone_score=raw_tone,
                    smoothed_tone=smoothed,
                    alpha=alpha,
                    days_since_prev=days_since_prev,
                )
            )
            prev_date = obs_date

        return series

    # ------------------------------------------------------------------
    # Ordered-probit nowcast
    # ------------------------------------------------------------------

    def predict_probabilities(
        self,
        smoothed_tone: float,
        core_cpi_yoy: float,
        unemployment_rate: float,
    ) -> dict[int, float]:
        """
        Ordered-probit probabilities over rate-move buckets (bps).

        Latent index:
            eta = beta_S  * S
                + beta_pi * (CPI_yoy - 2)
                + beta_u  * (U - U_baseline)

        For sorted cut points c_1 < ... < c_{K-1}:
            P(Y = j_1)        = Phi(c_1 - eta)
            P(Y = j_k)        = Phi(c_k - eta) - Phi(c_{k-1} - eta)
            P(Y = j_K)        = 1 - Phi(c_{K-1} - eta)
        """
        eta = (
            self.beta["tone_smoothed"] * smoothed_tone
            + self.beta["core_cpi_yoy_gap"]
              * (core_cpi_yoy - INFLATION_TARGET_PCT)
            + self.beta["unemployment_gap"]
              * (unemployment_rate - UNEMPLOYMENT_BASELINE_PCT)
        )

        cdfs = [0.0] + [_PHI(c - eta) for c in self.cuts] + [1.0]
        # Clamp tiny floating-point negatives that can arise from CDF subtraction.
        return {
            self.buckets_bps[i]: max(0.0, cdfs[i + 1] - cdfs[i])
            for i in range(len(self.buckets_bps))
        }

    def tone_implied_rate(
        self, probabilities: dict[int, float], current_rate: float
    ) -> float:
        """
        Expected next-meeting rate in pct:
            tone_implied_rate = current_rate + E[move_bps] / 100.
        """
        expected_bps = sum(prob * bps for bps, prob in probabilities.items())
        return current_rate + expected_bps / 100.0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _smooth_step(
        self,
        release_date: str | date | datetime,
        tone_score: float,
        prior_smoothed: float | None,
        prior_date: str | date | datetime | None,
    ) -> SmoothedTone:
        """Apply one EWMA step on top of an optional prior state."""
        obs_date = _parse_date(release_date)

        if prior_smoothed is None:
            return SmoothedTone(
                observation_date=obs_date,
                tone_score=tone_score,
                smoothed_tone=tone_score,
                alpha=1.0,
                days_since_prev=None,
            )

        if prior_date is None:
            raise ValueError(
                "prior_release_date is required when prior_smoothed_tone is provided"
            )

        days = (obs_date - _parse_date(prior_date)).days
        alpha = self._alpha(days)
        smoothed = alpha * tone_score + (1 - alpha) * prior_smoothed
        return SmoothedTone(
            observation_date=obs_date,
            tone_score=tone_score,
            smoothed_tone=smoothed,
            alpha=alpha,
            days_since_prev=days,
        )

    def _divergence(
        self, tone_implied: float, market_implied: float | None
    ) -> tuple[float | None, str]:
        """
        Signed gap between the market-implied and tone-implied next-meeting
        rate, in percentage points:

            divergence = market_implied - tone_implied

        Where:
            - tone_implied   is built from the EWMA-smoothed FOMC tone, the
              core-CPI gap, and the unemployment gap (eta -> ordered probit
              -> expected basis-point move on top of the current policy rate).
            - market_implied is the market proxy for the next-meeting rate,
              currently the 2-year US Treasury yield (DGS2).

        Label semantics describe the *direction* of disagreement only,
        not whether the market is correct:
            - divergence > 0  (market above tone):     "Market overestimates"
            - divergence < 0  (market below tone):     "Market underestimates"
            - |divergence| < DIVERGENCE_TOLERANCE_PCT: "aligned"

        Whether the market's expectation is right or wrong (using the
        current effective fed funds rate ER as the anchor) is computed
        separately in `_market_verdict`.
        """
        if market_implied is None:
            return None, "aligned"
        diff = market_implied - tone_implied
        if abs(diff) < DIVERGENCE_TOLERANCE_PCT:
            return diff, "aligned"
        return diff, "Divergence: market overestimates" if diff > 0 else "Divergence: market underestimates"

    def _market_verdict(
        self,
        tone_implied: float,
        market_implied: float | None,
        effective_rate: float | None,
    ) -> str | None:
        """
        Judge whether the market's next-meeting expectation is consistent
        with the tone-implied path, anchored on the current effective fed
        funds rate ER (FEDFUNDS, latest observation in macro_data).

        The verdict is a directional-agreement test: market is "right"
        when M and T fall on the same side of ER (both pricing a hike or
        both pricing a cut), and "wrong" when they fall on opposite sides.

        Notation: M = market_implied, T = tone_implied, ER = effective_rate.

        Rules:
            - M > ER (market pricing a hike):
                * T > ER → tone also pricing a hike → market RIGHT.
                * T < ER → tone pricing a cut → market WRONG.
            - M < ER (market pricing a cut):
                * T > ER → tone pricing a hike → market WRONG.
                * T < ER → tone also pricing a cut → market RIGHT.

        Returns None when there is no market proxy, no policy rate
        available, or when M ≈ ER within DIVERGENCE_TOLERANCE_PCT
        (market is effectively flat — nothing to judge).
        """
        if market_implied is None or effective_rate is None:
            return None
        if abs(market_implied - effective_rate) < DIVERGENCE_TOLERANCE_PCT:
            return None
        if market_implied > effective_rate:
            return "right" if tone_implied > effective_rate else "wrong"
        # market_implied < effective_rate
        return "wrong" if tone_implied > effective_rate else "right"

    def _narrative(
        self,
        probs: dict[int, float],
        divergence: float | None,
        direction: str,
        verdict: str | None = None,
    ) -> str:
        modal_bps, modal_p = max(probs.items(), key=lambda kv: kv[1])
        bits = [f"Modal next-meeting move: {modal_bps:+d} bps (p={modal_p:.2f})."]
        if divergence is not None:
            bits.append(
                f"Tone vs market proxy: {divergence:+.2f} pp ({direction})."
            )
        if verdict is not None:
            bits.append(
                f"Market expectations look {verdict} relative to the "
                f"current effective fed funds rate."
            )
        return " ".join(bits)

    def _alpha(self, days: int) -> float:
        return 1.0 - math.exp(-self._lambda * days)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# Database integration: run the strategist over fedwatcher.db and persist
# results into the signals table.
# ---------------------------------------------------------------------------

_REQUIRED_MACRO_COLUMNS = ("core_cpi_yoy", "unemployment_rate", "policy_rate")


def _connect_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database file not found: {db_path}. Run scripts/init_db.py first."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_signals_columns(conn: sqlite3.Connection) -> None:
    """Defensively add columns introduced by this runner if the existing DB predates them."""

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(signals)")}
    if not existing:
        raise RuntimeError(
            "signals table is missing. Run scripts/init_db.py with the latest schema."
        )
    if "smoothed_tone" not in existing:
        conn.execute("ALTER TABLE signals ADD COLUMN smoothed_tone REAL")
    if "market_verdict" not in existing:
        conn.execute("ALTER TABLE signals ADD COLUMN market_verdict TEXT")


def _fetch_sentiment_history(
    conn: sqlite3.Connection, sentiment_table: str
) -> list[dict[str, Any]]:
    """Tone history joined with the document release date, oldest first."""

    rows = conn.execute(
        f"""
        SELECT
            s.id            AS sentiment_id,
            s.document_id   AS document_id,
            s.tone_score    AS tone_score,
            d.release_date  AS release_date
        FROM {sentiment_table} s
        JOIN documents d ON d.id = s.document_id
        WHERE s.tone_score   IS NOT NULL
          AND d.release_date IS NOT NULL
        ORDER BY d.release_date ASC, s.id ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_signalled_document_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT document_id FROM signals").fetchall()
    return {int(r["document_id"]) for r in rows if r["document_id"] is not None}


def _fetch_macro_for_month(
    conn: sqlite3.Connection, observation_month: str
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT core_cpi_yoy, unemployment_rate, us2y_yield, policy_rate
        FROM macro_data
        WHERE observation_month = ?
        """,
        (observation_month,),
    ).fetchone()
    return dict(row) if row else None


def _insert_signal(conn: sqlite3.Connection, signal: PolicySignal) -> None:
    conn.execute(
        """
        INSERT INTO signals (
            document_id,
            smoothed_tone,
            tone_implied_next_rate,
            market_implied_next_rate,
            divergence,
            signal_direction,
            market_verdict,
            narrative
        ) VALUES (
            :document_id,
            :smoothed_tone,
            :tone_implied_next_rate,
            :market_implied_next_rate,
            :divergence,
            :signal_direction,
            :market_verdict,
            :narrative
        );
        """,
        signal.to_db_row(),
    )


def process_unprocessed_documents(
    db_path: Path = DEFAULT_DB_PATH,
    sentiment_table: str = DEFAULT_SENTIMENT_TABLE,
    dry_run: bool = False,
) -> int:
    """
    Run StrategistAgent over the full sentiment_w history and persist a
    signals row for every document that doesn't already have one.

    The EWMA chain is replayed from the start so that prior_smoothed_tone
    is consistent regardless of how many signals were previously written.
    Documents missing required macro inputs (policy_rate / core_cpi_yoy /
    unemployment_rate) are skipped with a warning but still update the
    smoothing state.

    Returns the count of newly inserted signal rows.
    """

    conn = _connect_db(db_path)
    _ensure_signals_columns(conn)
    conn.commit()

    agent = StrategistAgent()
    history = _fetch_sentiment_history(conn, sentiment_table)
    if not history:
        print(f"No sentiment rows found in {sentiment_table}.")
        conn.close()
        return 0

    already_signalled = _fetch_signalled_document_ids(conn)

    observations = [
        {"release_date": h["release_date"], "tone_score": h["tone_score"]}
        for h in history
    ]
    smoothed_series = agent.smooth_tones(observations)

    inserted = 0
    skipped = 0
    prior: SmoothedTone | None = None

    print(
        f"Loaded {len(history)} sentiment row(s) from {sentiment_table}. "
        f"{len(already_signalled)} already in signals."
    )

    try:
        for h, smoothed in zip(history, smoothed_series):
            doc_id = int(h["document_id"])

            if doc_id in already_signalled:
                prior = smoothed
                continue

            month = str(h["release_date"])[:7]
            macro_row = _fetch_macro_for_month(conn, month)

            if macro_row is None:
                print(
                    f"SKIP document_id={doc_id} ({h['release_date']}): "
                    f"no macro_data row for {month}.",
                    file=sys.stderr,
                )
                skipped += 1
                prior = smoothed
                continue

            missing = [
                column
                for column in _REQUIRED_MACRO_COLUMNS
                if macro_row.get(column) is None
            ]
            if missing:
                print(
                    f"SKIP document_id={doc_id} ({month}): missing macro "
                    f"fields: {', '.join(missing)}.",
                    file=sys.stderr,
                )
                skipped += 1
                prior = smoothed
                continue

            try:
                signal = agent.run(
                    sentiment={
                        "document_id": doc_id,
                        "tone_score": h["tone_score"],
                        "release_date": h["release_date"],
                    },
                    macro={
                        "core_cpi_yoy": macro_row["core_cpi_yoy"],
                        "unemployment_rate": macro_row["unemployment_rate"],
                    },
                    market={
                        "current_rate": macro_row["policy_rate"],
                        "us2y_yield": macro_row.get("us2y_yield"),
                    },
                    prior_smoothed_tone=prior.smoothed_tone if prior else None,
                    prior_release_date=prior.observation_date if prior else None,
                )

                market_implied_disp = (
                    f"{signal.market_implied_next_rate:.3f}"
                    if signal.market_implied_next_rate is not None
                    else "n/a"
                )
                divergence_disp = (
                    f"{signal.divergence:+.3f}"
                    if signal.divergence is not None
                    else "n/a"
                )
                print(
                    f"document_id={doc_id} {h['release_date']}: "
                    f"S={signal.smoothed_tone:+.3f} "
                    f"tone={signal.tone_implied_next_rate:.3f} "
                    f"market={market_implied_disp} "
                    f"div={divergence_disp} "
                    f"dir='{signal.signal_direction}' "
                    f"verdict={signal.market_verdict}"
                )

                if not dry_run:
                    _insert_signal(conn, signal)
                    conn.commit()
                    inserted += 1

            except Exception as exc:
                conn.rollback()
                print(
                    f"ERROR processing document_id={doc_id}: {exc}",
                    file=sys.stderr,
                )

            prior = smoothed
    finally:
        conn.close()

    print("-" * 80)
    print(
        f"Done. Inserted {inserted}, skipped {skipped}, "
        f"already-signalled {len(already_signalled)}."
    )
    return inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run StrategistAgent over sentiment_w + macro_data and "
            "persist results into the signals table."
        )
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite database path. Default: {DEFAULT_DB_PATH}",
    )
    parser.add_argument(
        "--sentiment-table",
        default=DEFAULT_SENTIMENT_TABLE,
        help=f"Tone source table. Default: {DEFAULT_SENTIMENT_TABLE}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute signals but do not write to the signals table.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    process_unprocessed_documents(
        db_path=Path(args.db),
        sentiment_table=args.sentiment_table,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
