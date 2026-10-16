"""Assemble the origin-target design table for one availability profile.

Rows: every market date ``D`` in the frozen history, origin ``D 00:00`` NEM
market time, horizons 1..24 (target ``origin + (h-1) h``). All rows are kept;
eligibility is expressed as flags, never by dropping rows.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from nem_forecast.contract import NEM_MARKET_TZ
from nem_forecast.features.calendar import civil_calendar, market_calendar
from nem_forecast.features.history import (
    FEATURES,
    HOUR_NS,
    NAT,
    Observations,
    compute_feature,
)

HORIZONS = 24
TARGET_COLUMN = "target_dispatch_totaldemand_hourly_mean_mw"
KEY = ["availability_profile", "origin_utc", "horizon"]


def _ts(ns: np.ndarray) -> pd.Series:
    out = pd.Series(ns, dtype="int64")
    return pd.to_datetime(out.where(out != NAT), utc=True)


def design_keys(first_date: dt.date, last_date: dt.date) -> pd.DataFrame:
    days = pd.date_range(first_date, last_date, freq="D")
    origins = (
        pd.DatetimeIndex(
            [dt.datetime.combine(d.date(), dt.time(0), tzinfo=NEM_MARKET_TZ) for d in days]
        )
        .tz_convert("UTC")
        .to_numpy(dtype="datetime64[ns]")
        .astype(np.int64)
    )
    origin = np.repeat(origins, HORIZONS)
    horizon = np.tile(np.arange(1, HORIZONS + 1, dtype=np.int64), len(origins))
    return pd.DataFrame(
        {"origin_ns": origin, "horizon": horizon, "target_ns": origin + (horizon - 1) * HOUR_NS}
    )


def build_profile(
    obs: Observations,
    keys: pd.DataFrame,
    profile: str,
    lag_hours: int,
    public_holidays: dict[dt.date, str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (wide design table, long lineage audit) for one profile."""
    origin = keys["origin_ns"].to_numpy(dtype=np.int64)
    target = keys["target_ns"].to_numpy(dtype=np.int64)
    cutoff = origin - np.int64(lag_hours) * HOUR_NS
    origin_utc, target_utc, cutoff_utc = _ts(origin), _ts(target), _ts(cutoff)

    pos = (target - obs.first_label) // HOUR_NS
    in_range = (target >= obs.first_label) & (pos < len(obs.values))
    safe = np.clip(pos, 0, len(obs.values) - 1)
    target_available = in_range & obs.complete[safe]

    wide = pd.DataFrame(
        {
            "availability_profile": pd.Series(profile, index=keys.index, dtype="string"),
            "origin_utc": origin_utc,
            "origin_market": origin_utc.dt.tz_convert(NEM_MARKET_TZ),
            "horizon": keys["horizon"].astype("int8"),
            "target_hour_utc": target_utc,
            "target_hour_market": target_utc.dt.tz_convert(NEM_MARKET_TZ),
            "cutoff_utc": cutoff_utc,
            "availability_lag_hours": np.int8(lag_hours),
            TARGET_COLUMN: np.where(target_available, obs.values[safe], np.nan),
            "target_available": target_available,
        }
    )
    wide = pd.concat(
        [wide, market_calendar(target_utc), civil_calendar(target_utc, public_holidays)], axis=1
    )

    audits = []
    for feature in FEATURES:
        result = compute_feature(obs, feature, target, cutoff)
        wide[feature.name] = result["value"].to_numpy()
        wide[f"{feature.name}__available"] = result["available"].to_numpy()
        wide[f"{feature.name}__missing_reason"] = result["missing_reason"].astype("string")
        latest = result["latest_source_endpoint_ns"].to_numpy()
        audits.append(
            pd.DataFrame(
                {
                    "availability_profile": wide["availability_profile"],
                    "origin_utc": origin_utc,
                    "horizon": wide["horizon"],
                    "target_hour_utc": target_utc,
                    "feature": pd.Series(feature.name, index=keys.index, dtype="string"),
                    "value": result["value"],
                    "available": result["available"],
                    "missing_reason": result["missing_reason"].astype("string"),
                    "represented_start_label_utc": _ts(result["represented_start_ns"].to_numpy()),
                    "represented_end_label_utc": _ts(result["represented_end_ns"].to_numpy()),
                    "n_required_observations": result["n_required"],
                    "n_used_observations": result["n_used"],
                    "latest_observation_label_utc": _ts(result["latest_observation_ns"].to_numpy()),
                    "latest_source_endpoint_utc": _ts(latest),
                    "cutoff_utc": cutoff_utc,
                    "endpoint_le_cutoff": pd.Series(latest <= cutoff, dtype="boolean").where(
                        latest != NAT
                    ),
                }
            )
        )

    hist_ok = np.ones(len(wide), dtype=bool)
    for feature in FEATURES:
        structural = wide[f"{feature.name}__missing_reason"] == "after_cutoff"
        hist_ok &= (wide[f"{feature.name}__available"] | structural).to_numpy(dtype=bool)
    wide["eligible_lag24_baseline"] = wide["target_available"] & wide["lag_24h__available"]
    wide["eligible_lag168_baseline"] = wide["target_available"] & wide["lag_168h__available"]
    wide["eligible_initial_ml_features"] = wide["target_available"] & hist_ok
    wide["year"] = wide["target_hour_market"].dt.year.astype("int16")

    wide = wide.sort_values(["origin_utc", "horizon"], kind="mergesort").reset_index(drop=True)
    audit = (
        pd.concat(audits, ignore_index=True)
        .sort_values(["origin_utc", "horizon", "feature"], kind="mergesort")
        .reset_index(drop=True)
    )
    audit["year"] = audit["target_hour_utc"].dt.tz_convert(NEM_MARKET_TZ).dt.year.astype("int16")
    return wide, audit
