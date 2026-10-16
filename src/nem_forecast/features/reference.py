"""Independent per-origin reference builder on physically truncated input.

For each forecast origin, the observation arrays are **sliced** so that only
hours whose actual latest source endpoint is ``<= cutoff`` remain. Later data
are not merely masked; they are absent from the object the features are
computed from. Window definitions are re-implemented here (not imported from
:mod:`history`), so the vectorised builder is checked by a separate code path.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from nem_forecast.features.history import FEATURES, HOUR_NS, Observations

HOUR = int(HOUR_NS)  # plain ints: reference arithmetic is independent of numpy types
DAY = 24 * HOUR


def _labels(name: str, target: int, cutoff: int) -> list[int]:
    if name.startswith("lag_"):
        return [target - int(name[4:-1]) * HOUR]
    if name.startswith("recent_"):
        hours = int(name.split("_")[1][:-1])
        last = cutoff - HOUR
        return [last - i * HOUR for i in range(hours - 1, -1, -1)]
    if name == "same_hour_mean_7d":
        k = 1
        while target - k * DAY + HOUR > cutoff:
            k += 1
        return [target - (k + j) * DAY for j in range(7)]
    if name == "same_hour_same_weekday_mean_4w":
        return [target - k * 7 * DAY for k in range(1, 5)]
    raise ValueError(name)


def _agg(name: str, values: np.ndarray) -> float:
    if name.endswith("_std"):
        mean = values.sum() / len(values)
        return float(np.sqrt(((values - mean) ** 2).sum() / (len(values) - 1)))
    if name.endswith("_min"):
        return float(values.min())
    if name.endswith("_max"):
        return float(values.max())
    return float(values.sum() / len(values))


def truncated_view(obs: Observations, cutoff: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Physically truncated copy: only complete hours whose endpoint is <= cutoff.

    Boolean indexing returns new arrays; observations after the cutoff are not
    present in the returned objects.
    """
    keep = obs.complete & (obs.endpoint <= cutoff)
    labels = obs.first_label + np.arange(len(obs.values), dtype=np.int64) * HOUR_NS
    return labels[keep], obs.values[keep], obs.endpoint[keep]


def reference_features(
    obs: Observations, origins: np.ndarray, lag_hours: int, horizons: int = 24
) -> pd.DataFrame:
    """Long table (origin, horizon, feature, value, available, reason, latest endpoint)."""
    first = int(obs.first_label)
    rows = []
    for origin in origins.tolist():
        cutoff = origin - lag_hours * HOUR_NS
        vis_labels, vis_values, vis_ends = truncated_view(obs, cutoff)
        assert vis_ends.size == 0 or int(vis_ends.max()) <= cutoff
        for horizon in range(1, horizons + 1):
            target = origin + (horizon - 1) * HOUR_NS
            for feature in FEATURES:
                labels = np.array(_labels(feature.name, target, cutoff), dtype=np.int64)
                idx = np.clip(np.searchsorted(vis_labels, labels), 0, max(len(vis_labels) - 1, 0))
                found = (
                    (vis_labels[idx] == labels) if len(vis_labels) else np.zeros(len(labels), bool)
                )
                if bool((labels + HOUR_NS > cutoff).any()):
                    reason = "after_cutoff"
                elif bool((labels < first).any()):
                    reason = "insufficient_history"
                elif not bool(np.all(found)):
                    reason = "missing_source_hour"
                else:
                    reason = ""
                if reason:
                    value, latest = math.nan, None
                else:
                    value = _agg(feature.name, vis_values[idx])
                    latest = int(vis_ends[idx].max())
                rows.append((origin, horizon, feature.name, value, not reason, reason, latest))
    return pd.DataFrame(
        rows,
        columns=[
            "origin_ns",
            "horizon",
            "feature",
            "value",
            "available",
            "missing_reason",
            "latest_source_endpoint_ns",
        ],
    )
