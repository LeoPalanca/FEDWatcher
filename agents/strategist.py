"""
StrategistAgent – EWMA tone smoothing.

Smooths an irregular series of Fed tone scores through time using a
time-aware exponentially weighted moving average:

    S_t      = alpha_t * tone_score_t + (1 - alpha_t) * S_{t-1}
    alpha_t  = 1 - exp(-lambda * Delta t)
    lambda   = ln(2) / h           (h = half-life in days, default 21)

Delta t is the number of calendar days between consecutive documents, so
the smoother handles the irregular FOMC release cadence correctly: long
gaps between meetings let prior tone decay, back-to-back releases stay
close to the previous level.

This module currently implements only the smoothing step. Multinomial
nowcasting, tone-implied rate, and market divergence are deferred until
macro and market features are wired in pipeline.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable


DEFAULT_HALFLIFE_DAYS = 21


@dataclass
class SmoothedTone:
    """One smoothed observation in a tone time series."""

    observation_date: date
    tone_score: float
    smoothed_tone: float
    alpha: float
    days_since_prev: int | None


class StrategistAgent:
    """
    Time-aware EWMA smoothing of Fed tone scores.

    Usage:
        agent = StrategistAgent()
        series = agent.smooth_tones([
            {"release_date": "2025-01-29", "tone_score":  0.10},
            {"release_date": "2025-03-19", "tone_score":  0.40},
            {"release_date": "2025-05-07", "tone_score": -0.20},
        ])
        # series[-1].smoothed_tone is the current smoothed tone S_t.
    """

    def __init__(self, halflife_days: float = DEFAULT_HALFLIFE_DAYS) -> None:
        self.halflife_days = halflife_days
        self._lambda = math.log(2) / halflife_days

    # ------------------------------------------------------------------
    # EWMA tone smoothing
    # ------------------------------------------------------------------

    def smooth_tones(
        self, observations: Iterable[dict[str, Any]]
    ) -> list[SmoothedTone]:
        """
        Smooth a chronologically irregular tone series.

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
