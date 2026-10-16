"""Readable Phase 4 reports: Markdown tables and static diagnostic plots.

Plots use small multiples (one panel per model, the model highlighted in a
single hue over grey context lines) rather than a 4-hue categorical palette:
no 4-hue set from the reference palette passed the all-pairs colour checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from nem_forecast.evaluation.folds import Fold

SURFACE, INK, INK2, GRID, HIGHLIGHT, CONTEXT = (
    "#fcfcfb",
    "#0b0b0b",
    "#52514e",
    "#e4e3df",
    "#2a78d6",
    "#c9c8c2",
)
METRIC_COLS = [
    "n",
    "mae_mw",
    "rmse_mw",
    "wape",
    "mean_signed_error_mw",
    "evening_mae_mw",
    "high_demand_mae_mw",
    "pinball_p10",
    "pinball_p50",
    "pinball_p90",
    "coverage_p10_p90",
    "mean_interval_width_mw",
    "crossing_rate",
]


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 10 else f"{v:.2f}"
    return "" if v is None else str(v)


def _md(rows: list[dict[str, Any]], cols: list[str]) -> str:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(out)


def _small_multiples(
    series: dict[str, pd.Series],
    title: str,
    ylabel: str,
    path: Path,
    reference: float | None = None,
) -> None:
    models = list(series)
    fig, axes = plt.subplots(
        1, len(models), figsize=(3.2 * len(models), 3.0), sharey=True, facecolor=SURFACE
    )
    for ax, model in zip(axes, models, strict=True):
        ax.set_facecolor(SURFACE)
        for other in models:
            if other != model:
                s = series[other]
                ax.plot(s.index, s.values, color=CONTEXT, linewidth=1.2, zorder=1)
        s = series[model]
        ax.plot(s.index, s.values, color=HIGHLIGHT, linewidth=2, marker="o", markersize=3, zorder=3)
        if reference is not None:
            ax.axhline(reference, color=INK2, linewidth=1, linestyle="--", zorder=2)
        ax.set_title(model, color=INK, fontsize=10)
        ax.grid(color=GRID, linewidth=0.6)
        ax.tick_params(colors=INK2, labelsize=8)
        for spine in ax.spines.values():
            spine.set_visible(False)
    axes[0].set_ylabel(ylabel, color=INK2)
    fig.suptitle(title, color=INK, fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)


def write_readable_reports(
    run_dir: Path,
    results: dict[str, Any],
    fits: list[dict[str, Any]],
    offsets: pd.DataFrame,
    ev: pd.DataFrame,
    folds: list[Fold],
) -> None:
    lines = [
        "# Phase 4 development backtest (baselines)",
        "",
        "Development origins only (2025-07-01 … 2026-06-30). The final holdout was not evaluated.",
        "",
        "## Coverage (all expected validation rows)",
        "",
    ]
    cov = [
        {
            "model": m,
            **{k: v for k, v in c.items() if k != "missing_by_reason"},
            "missing_by_reason": c["missing_by_reason"],
        }
        for m, c in results["coverage"].items()
    ]
    lines += [
        _md(
            cov,
            [
                "model",
                "expected_rows",
                "predicted_rows",
                "row_coverage",
                "origins",
                "origins_with_all_24_horizons",
                "origin_coverage",
                "missing_by_reason",
            ],
        ),
        "",
    ]
    lines += [
        f"## Full-horizon operational comparison (models with all 24 horizons: "
        f"{', '.join(results['full_horizon_models'])})",
        "",
    ]
    lines += [
        _md(
            [{"model": m, **r} for m, r in results["full_horizon_comparison"].items()],
            ["model", *METRIC_COLS],
        ),
        "",
    ]
    lines += [
        f"## Common-row comparison ({results['common_rows']} identical rows; horizons "
        f"{results['common_row_horizons'][0]}-{results['common_row_horizons'][-1]})",
        "",
    ]
    lines += [
        _md(
            [{"model": m, **r} for m, r in results["common_row_comparison"].items()],
            ["model", *METRIC_COLS],
        ),
        "",
    ]
    lines += ["## Paired daily-block bootstrap (MAE A - B, MW; negative = A better)", ""]
    lines += [
        _md(
            results["paired_bootstrap"],
            [
                "model_a",
                "model_b",
                "row_set",
                "mae_difference_mw",
                "ci_low",
                "ci_high",
                "share_replicates_a_worse",
                "dates",
                "rows",
                "replicates",
                "seed",
            ],
        ),
        "",
    ]
    lines += ["## Hybrid weights per fold", ""]
    lines += [
        _md(
            [f for f in fits if f["model"] == "day_week_hybrid"],
            ["fold_id", "weight", "fit_rows", "fit_first_origin", "fit_last_origin"],
        ),
        "",
    ]
    lines += ["## Folds and temporal cutoffs", ""]
    lines += [_md([f.as_dict() for f in folds], list(folds[0].as_dict())), ""]
    (run_dir / "metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    slice_lines = ["# Slices (common rows, identical for every model)", ""]
    for dim, rows in results["slices_common_rows"].items():
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
                    "coverage_p10_p90",
                    "mean_interval_width_mw",
                ],
            ),
            "",
        ]
    slice_lines += [
        "## By horizon (each model's own available rows)",
        "",
        _md(
            results["by_horizon_own_rows"],
            ["model", "horizon", "n", "mae_mw", "coverage_p10_p90", "mean_interval_width_mw"],
        ),
        "",
    ]
    (run_dir / "slices.md").write_text("\n".join(slice_lines) + "\n", encoding="utf-8")

    cal = (
        offsets.groupby(["model", "horizon"])
        .agg(
            min_samples=("n_samples", "min"),
            max_samples=("n_samples", "max"),
            mean_q10=("offset_q10", "mean"),
            mean_q50=("offset_q50", "mean"),
            mean_q90=("offset_q90", "mean"),
        )
        .reset_index()
    )
    (run_dir / "residual_quantiles.md").write_text(
        "# Residual quantile offsets (mean across folds; per fold in residual_quantiles.json)\n\n"
        + _md(
            [{str(k): v for k, v in r.items()} for r in cal.to_dict("records")],
            [str(c) for c in cal.columns],
        )
        + "\n",
        encoding="utf-8",
    )

    plots = run_dir / "plots"
    plots.mkdir()
    by_h = pd.DataFrame(results["by_horizon_own_rows"])
    _small_multiples(
        {str(m): g.set_index("horizon")["mae_mw"] for m, g in by_h.groupby("model")},
        "MAE by horizon (development folds, own available rows)",
        "MAE (MW)",
        plots / "mae_by_horizon.png",
    )
    _small_multiples(
        {str(m): g.set_index("horizon")["coverage_p10_p90"] for m, g in by_h.groupby("model")},
        "p10-p90 empirical coverage by horizon (nominal 0.80, dashed)",
        "coverage",
        plots / "coverage_by_horizon.png",
        reference=0.8,
    )
    by_f = pd.DataFrame(results["by_fold_own_rows"])
    _small_multiples(
        {str(m): g.set_index("fold_id")["mae_mw"] for m, g in by_f.groupby("model")},
        "MAE by development fold (1 = 2025-07 … 12 = 2026-06)",
        "MAE (MW)",
        plots / "mae_by_fold.png",
    )
