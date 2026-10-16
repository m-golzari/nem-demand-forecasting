"""Readable Phase 6 reports. Conditional calibration is shown before pooled numbers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from nem_forecast.evaluation.report import _md, _small_multiples

KEY_METRICS = [
    "n",
    "mae_mw",
    "rmse_mw",
    "wape",
    "mean_signed_error_mw",
    "mean_pinball",
    "pinball_p10",
    "pinball_p50",
    "pinball_p90",
    "coverage_p10_p90",
    "mean_interval_width_mw",
    "crossing_rate",
    "crossing_mean_magnitude_mw",
    "evening_mae_mw",
    "high_demand_mae_mw",
]


def _cov_table(rows: list[dict[str, Any]], dim: str) -> str:
    frame = pd.DataFrame(rows).pivot(index=dim, columns="model", values="coverage_p10_p90")
    table = [
        {dim: str(i), **{str(m): round(float(v), 3) for m, v in r.items()}}
        for i, r in frame.iterrows()
    ]
    return _md(table, [dim, *[str(c) for c in frame.columns]])


def write_phase6_reports(
    run_dir: Path,
    results: dict[str, Any],
    fold_log: list[dict[str, Any]],
    offsets: pd.DataFrame,
    ev: pd.DataFrame,
) -> None:
    s = results["slices_common_rows"]
    lines = [
        "# Phase 6 LightGBM development run",
        "",
        "Development origins 2025-07-01 .. 2026-06-30 only; the final holdout was not loaded or "
        "evaluated. These are development results, not generalisation claims.",
        "",
        "## Conditional calibration first: p10-p90 coverage (nominal 0.80) on identical rows",
        "",
        "### By season",
        "",
        _cov_table(s["season"], "season"),
        "",
        "### By fold (1 = 2025-07 .. 12 = 2026-06)",
        "",
        _cov_table(s["fold_id"], "fold_id"),
        "",
        "### By day type",
        "",
        _cov_table(s["day_type"], "day_type"),
        "",
        "### By training-threshold high demand",
        "",
        _cov_table(s["is_high_demand"], "is_high_demand"),
        "",
        "## Full-horizon comparison (identical rows; ranking: MAE, then mean pinball)",
        "",
        _md(
            [{"model": m, **r} for m, r in results["full_horizon_comparison"].items()],
            ["model", *KEY_METRICS],
        ),
        "",
        "## Coverage accounting",
        "",
        _md(
            [
                {"model": m, **{k: v for k, v in c.items() if k != "missing_by_reason"}}
                for m, c in results["coverage"].items()
            ],
            [
                "model",
                "expected_rows",
                "predicted_rows",
                "row_coverage",
                "origins_with_all_24_horizons",
            ],
        ),
        "",
        "## Paired market-date bootstrap: MAE (A - B, MW; negative = A better)",
        "",
        _md(
            results["paired_bootstrap"],
            [
                "model_a",
                "model_b",
                "row_set",
                "mae_difference_mw",
                "ci_low",
                "ci_high",
                "dates",
                "rows",
                "seed",
            ],
        ),
        "",
        "## Paired market-date bootstrap: mean pinball (A - B, MW) vs frozen hybrid",
        "",
        _md(
            results["paired_bootstrap_mean_pinball_vs_frozen_hybrid"],
            ["model_a", "model_b", "mae_difference_mw", "ci_low", "ci_high", "dates", "rows"],
        ),
        "",
        "(column `mae_difference_mw` here holds the mean-pinball difference)",
        "",
        "## Raw LightGBM quantile crossing (before any calibration or correction)",
        "",
        _md(
            [{"model": m, **v} for m, v in results["raw_quantile_crossing"].items()],
            ["model", "rate", "mean_magnitude_mw"],
        ),
        "",
        "## After rearrangement (sorted p10 <= p50 <= p90; reported variant only)",
        "",
        _md(
            [{"model": m, **r} for m, r in results["after_rearrangement"].items()],
            ["model", "mae_mw", "mean_pinball", "coverage_p10_p90", "crossing_rate"],
        ),
        "",
        "## Selected configurations, iterations and season fallback per fold",
        "",
        _md(
            [
                {
                    "fold": f["fold"]["fold_id"],
                    **f["structure"],
                    **{f"iter_{k}": v for k, v in f["iterations"].items()},
                    "inner_mae_mw": round(f["selected"]["inner_mae_mw"], 2),
                    "lgb_season_fallback_cells": f["season_fallback"]["lightgbm"],
                    "hybrid_season_fallback_cells": f["season_fallback"]["hybrid"],
                    "archive_months": len(f["archive_months"]),
                    "train_rows_final": f["rows"]["inner_train"],
                }
                for f in fold_log
            ],
            [
                "fold",
                "num_leaves",
                "min_data_in_leaf",
                "lambda_l2",
                "iter_p50",
                "iter_p10",
                "iter_p90",
                "inner_mae_mw",
                "lgb_season_fallback_cells",
                "hybrid_season_fallback_cells",
                "archive_months",
            ],
        ),
        "",
        "## Runtime (seconds per fold)",
        "",
        _md(
            [
                {
                    "fold": f["fold"]["fold_id"],
                    **{k: round(v, 1) for k, v in f["timing_seconds"].items()},
                }
                for f in fold_log
            ],
            [
                "fold",
                "tuning",
                "trailing_calibration",
                "season_archive",
                "final_fit",
                "final_predict",
            ],
        ),
        "",
    ]
    imp = pd.DataFrame([f["importance_gain"] for f in fold_log]).mean().sort_values(ascending=False)
    share = (imp / imp.sum()).round(4)
    lines += [
        "## Feature importance (final p50 models, mean gain share across folds)",
        "",
        "**Descriptive only, not causal.** Gain reflects how the fitted trees split; it does "
        "not measure effects on demand.",
        "",
        _md(
            [{"feature": str(k), "gain_share": float(v)} for k, v in share.items()],
            ["feature", "gain_share"],
        ),
        "",
    ]
    (run_dir / "metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    slice_lines = ["# Phase 6 slices (identical rows for every candidate)", ""]
    for dim, rows in s.items():
        slice_lines += [
            f"## {dim}",
            "",
            _md(
                rows,
                [
                    "model",
                    dim,
                    "n",
                    "mae_mw",
                    "wape",
                    "mean_signed_error_mw",
                    "mean_pinball",
                    "coverage_p10_p90",
                    "mean_interval_width_mw",
                ],
            ),
            "",
        ]
    (run_dir / "slices.md").write_text("\n".join(slice_lines) + "\n", encoding="utf-8")
    plots = run_dir / "plots"
    plots.mkdir()
    by_h = pd.DataFrame(results["by_horizon_own_rows"])
    _small_multiples(
        {str(m): g.set_index("horizon")["mae_mw"] for m, g in by_h.groupby("model")},
        "MAE by horizon (development folds)",
        "MAE (MW)",
        plots / "mae_by_horizon.png",
    )
    by_f = pd.DataFrame(s["fold_id"])
    _small_multiples(
        {str(m): g.set_index("fold_id")["coverage_p10_p90"] for m, g in by_f.groupby("model")},
        "p10-p90 coverage by fold (nominal 0.80, dashed)",
        "coverage",
        plots / "coverage_by_fold.png",
        reference=0.8,
    )
    _small_multiples(
        {str(m): g.set_index("fold_id")["mean_pinball"] for m, g in by_f.groupby("model")},
        "Mean pinball loss by fold",
        "mean pinball (MW)",
        plots / "mean_pinball_by_fold.png",
    )
