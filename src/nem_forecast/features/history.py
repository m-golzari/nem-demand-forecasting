"""Point-in-time historical demand features (vectorised over the full history).

Every feature is defined by the set of **hourly observation labels** it
requires. Observation ``H`` (canonical hour ``[H, H+1h)``) is built from source
intervals ending through ``H+1h``. Its actual latest source endpoint is the
parsed ``last_source_settlementdate`` from Phase 2. A required observation is
usable for a row only if its *actual* latest source endpoint is
``<= cutoff``, where ``cutoff = origin - availability_lag``. The cutoff is never
applied to hourly labels alone.

Missingness reasons, in order of precedence:

* ``after_cutoff``: a required observation's source endpoint (``H+1h``) is
  later than the cutoff (structural; e.g. ``lag_24h`` at horizon 24 under
  ``primary_operational``);
* ``insufficient_history``: a required label precedes the first canonical hour;
* ``missing_source_hour``: a required hour is absent or incomplete in Phase 2;
* ``source_endpoint_after_cutoff``: defensive; an observation exists but its
  actual endpoint exceeds the cutoff (impossible under the Phase 2 contract).

Values are null whenever unavailable: no substitution, no partial windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

HOUR_NS = np.int64(3_600_000_000_000)
NAT = np.iinfo(np.int64).min
Agg = Literal["value", "mean", "std", "min", "max"]


@dataclass(frozen=True)
class HistoricalFeature:
    name: str
    kind: Literal["lag", "recent_window", "same_hour_recent", "same_hour_weekday"]
    size: int  # lag hours, window hours, or number of days/weeks
    agg: Agg


FEATURES: tuple[HistoricalFeature, ...] = (
    HistoricalFeature("lag_24h", "lag", 24, "value"),
    HistoricalFeature("lag_48h", "lag", 48, "value"),
    HistoricalFeature("lag_168h", "lag", 168, "value"),
    HistoricalFeature("recent_24h_mean", "recent_window", 24, "mean"),
    HistoricalFeature("recent_24h_std", "recent_window", 24, "std"),
    HistoricalFeature("recent_24h_min", "recent_window", 24, "min"),
    HistoricalFeature("recent_24h_max", "recent_window", 24, "max"),
    HistoricalFeature("recent_168h_mean", "recent_window", 168, "mean"),
    HistoricalFeature("recent_168h_std", "recent_window", 168, "std"),
    HistoricalFeature("same_hour_mean_7d", "same_hour_recent", 7, "mean"),
    HistoricalFeature("same_hour_same_weekday_mean_4w", "same_hour_weekday", 4, "mean"),
)
REASONS = (
    "after_cutoff",
    "insufficient_history",
    "missing_source_hour",
    "source_endpoint_after_cutoff",
)


@dataclass(frozen=True)
class Observations:
    """Hourly observations on a regular grid, as int64 UTC nanoseconds."""

    first_label: np.int64
    values: np.ndarray  # float64, NaN if not complete
    complete: np.ndarray  # bool
    endpoint: np.ndarray  # int64 ns of actual latest source endpoint, NAT if not complete

    @classmethod
    def from_hourly(cls, hourly: pd.DataFrame, value_column: str) -> Observations:
        """Build from Phase 2 canonical hourly rows (must be a gap-free hourly grid)."""
        frame = hourly.sort_values("hour_start_utc").reset_index(drop=True)
        labels = _ns(frame["hour_start_utc"])
        if len(labels) and not (np.diff(labels) == HOUR_NS).all():
            raise ValueError("canonical hourly input must be a gap-free hourly grid")
        complete = frame["is_complete"].to_numpy(dtype=bool)
        endpoint_market = pd.to_datetime(
            frame["last_source_settlementdate"], format="%Y/%m/%d %H:%M:%S", errors="coerce"
        ).dt.tz_localize("+10:00")
        endpoint = np.where(complete, _ns(endpoint_market), NAT)
        values = np.where(complete, frame[value_column].to_numpy(dtype=float), np.nan)
        return cls(np.int64(labels[0]), values, complete, endpoint.astype(np.int64))


def _ns(series: pd.Series) -> np.ndarray:
    values = pd.to_datetime(series, utc=True)
    out = values.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    out[values.isna().to_numpy()] = NAT
    return out


def required_labels(
    feature: HistoricalFeature, target: np.ndarray, cutoff: np.ndarray
) -> np.ndarray:
    """Matrix (rows x k) of required observation labels (int64 UTC ns)."""
    t = target[:, None]
    if feature.kind == "lag":
        return t - feature.size * HOUR_NS
    if feature.kind == "recent_window":
        latest = cutoff - HOUR_NS  # latest label whose endpoint (label + 1h) <= cutoff
        offsets = np.arange(feature.size - 1, -1, -1, dtype=np.int64) * HOUR_NS
        return latest[:, None] - offsets[None, :]
    if feature.kind == "same_hour_recent":
        day = 24 * HOUR_NS
        # smallest k >= 1 whose label target - k days has endpoint <= cutoff
        first_k = np.maximum(1, -((cutoff - (target + HOUR_NS)) // day))
        ks = first_k[:, None] + np.arange(feature.size, dtype=np.int64)[None, :]
        return np.asarray(t - ks * day, dtype=np.int64)
    if feature.kind == "same_hour_weekday":
        ks = np.arange(1, feature.size + 1, dtype=np.int64)[None, :]
        return np.asarray(t - ks * 168 * HOUR_NS, dtype=np.int64)
    raise ValueError(feature.kind)


def aggregate(values: np.ndarray, agg: Agg) -> np.ndarray:
    with np.errstate(all="ignore"):
        if agg in ("value", "mean"):
            out = values.mean(axis=1)
        elif agg == "std":
            out = values.std(axis=1, ddof=1)
        elif agg == "min":
            out = values.min(axis=1)
        else:
            out = values.max(axis=1)
    return np.asarray(out, dtype=float)


def compute_feature(
    obs: Observations, feature: HistoricalFeature, target: np.ndarray, cutoff: np.ndarray
) -> pd.DataFrame:
    """Value, availability, reason and lineage for one feature over all rows."""
    labels = required_labels(feature, target, cutoff)
    pos = (labels - obs.first_label) // HOUR_NS
    in_range = (labels >= obs.first_label) & (pos < len(obs.values))
    safe = np.clip(pos, 0, len(obs.values) - 1)
    present = in_range & obs.complete[safe]
    endpoint = np.where(present, obs.endpoint[safe], NAT)
    values = np.where(present, obs.values[safe], np.nan)

    after = ((labels + HOUR_NS) > cutoff[:, None]).any(axis=1)
    before = (labels < obs.first_label).any(axis=1)
    missing = (~present).any(axis=1)
    endpoint_late = (present & (endpoint > cutoff[:, None])).any(axis=1)
    available = ~after & ~before & ~missing & ~endpoint_late
    reason = np.select([after, before, missing, endpoint_late], list(REASONS), default="").astype(
        object
    )

    value = np.where(available, aggregate(values, feature.agg), np.nan)
    latest_endpoint = np.where(available, endpoint.max(axis=1), NAT)
    return pd.DataFrame(
        {
            "value": value,
            "available": available,
            "missing_reason": reason,
            "represented_start_ns": labels.min(axis=1),
            "represented_end_ns": labels.max(axis=1),
            "n_required": np.full(len(target), labels.shape[1], dtype=np.int64),
            "n_used": np.where(available, labels.shape[1], 0).astype(np.int64),
            "latest_observation_ns": np.where(available, labels.max(axis=1), NAT),
            "latest_source_endpoint_ns": latest_endpoint,
        }
    )
