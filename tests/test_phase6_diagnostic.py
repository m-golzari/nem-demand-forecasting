"""Explanatory diagnostic: hand-calculated row quantities, identities and formula checks."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nem_forecast.evaluation.bootstrap import paired_block_bootstrap_mean
from nem_forecast.evaluation.phase6_diagnostic import (
    bias_attribution,
    formula_checks,
    row_quantities,
)


def _ev() -> pd.DataFrame:
    rows = []
    for model, off in (
        ("lightgbm_raw", 0.0),
        ("lightgbm_trailing_calibrated", -5.0),
        ("lightgbm_season_calibrated", -20.0),
        ("day_week_hybrid_season_calibrated", 0.0),
    ):
        for h, (y, raw) in enumerate([(100.0, 110.0), (100.0, 80.0), (200.0, 200.0)], start=1):
            rows.append(
                {
                    "model": model,
                    "fold_id": 1,
                    "horizon": h,
                    "origin_date": f"d{h % 2}",
                    "origin_utc": h,
                    "actual": y,
                    "raw_p10": raw - 20,
                    "raw_p50": raw,
                    "raw_p90": raw + 20,
                    "p10": raw - 20 + off,
                    "p50": raw + off,
                    "p90": raw + 20 + off,
                }
            )
    return pd.DataFrame(rows)


def _offsets() -> pd.DataFrame:
    parts = []
    for cal, off, col in (
        ("lightgbm_trailing", -5.0, "offset"),
        ("lightgbm_season", -20.0, "applied_offset"),
        ("hybrid_season", 0.0, "applied_offset"),
    ):
        for h in (1, 2, 3):
            for q in (0.1, 0.5, 0.9):
                parts.append(
                    {
                        "calibration": cal,
                        "fold_id": 1,
                        "horizon": h,
                        "quantile": q,
                        col: off,
                        "n_samples": 180 if cal == "lightgbm_trailing" else 200,
                        "source": "trailing" if cal == "lightgbm_trailing" else "season",
                    }
                )
    return pd.DataFrame(parts)


def test_row_quantities_hand_calculated() -> None:
    q = row_quantities(_ev())
    raw = q[q["model"] == "lightgbm_raw"].reset_index(drop=True)
    # raw rows: [90,130] holds 100; [60,100] holds 100 (boundary); [180,220] holds 200
    assert list(raw["covered"]) == [1.0, 1.0, 1.0]
    trail = q[q["model"] == "lightgbm_season_calibrated"].reset_index(drop=True)
    # season offset -20: [70,110] holds 100; [40,80] misses 100 above; [160,200] holds 200
    assert list(trail["upper_miss"]) == [0.0, 1.0, 0.0] and list(trail["lower_miss"]) == [
        0.0,
        0.0,
        0.0,
    ]
    assert list(raw["signed_error"]) == [10.0, -20.0, 0.0] and list(raw["width"]) == [
        40.0,
        40.0,
        40.0,
    ]
    # mean pinball row 1: p10=90 (0.1*10=1), p50=110 (0.5*10=5), p90=130 (0.1*30=3) -> 3
    assert raw["mean_pinball"].iloc[0] == pytest.approx(3.0)


def test_bias_attribution_identity() -> None:
    rows = bias_attribution(row_quantities(_ev()))
    season = next(r for r in rows if r["model"] == "lightgbm_season_calibrated")
    assert season["mean_p50_offset_mw"] == pytest.approx(-20.0)
    assert season["calibrated_bias_mw"] == pytest.approx(season["raw_p50_bias_mw"] - 20.0)
    assert abs(season["identity_error_mw"]) < 1e-9


def test_formula_checks_pass_and_detect_tampering() -> None:
    ev, off = _ev(), _offsets()
    checks = formula_checks(ev, off)
    assert all(
        checks[m]["passed"] for m in ("lightgbm_trailing_calibrated", "lightgbm_season_calibrated")
    )
    tampered = ev.copy()
    tampered.loc[tampered["model"] == "lightgbm_trailing_calibrated", "p50"] += 0.5
    assert not formula_checks(tampered, off)["lightgbm_trailing_calibrated"]["passed"]


def test_paired_bootstrap_mean_signed_hand_calculated() -> None:
    a = pd.Series([1.0, 1.0, -3.0])
    b = pd.Series([0.0, 0.0, 0.0])
    res = paired_block_bootstrap_mean(a, b, pd.Series(["d1", "d1", "d2"]), 400, 3)
    assert res["difference"] == pytest.approx(-1 / 3)  # signed values, not absolute
    assert res["ci_low"] >= -3.0 and res["ci_high"] <= 1.0
    assert np.isfinite(res["ci_low"])
