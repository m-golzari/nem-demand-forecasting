# Phase 4 report: seasonal baselines and rolling development backtest

Date: 2026-10-16 · Status: **complete and approved by the owner (2026-10-16).**

- **Data used:** development origins 2025-07-01 … 2026-06-30 only. The final
  holdout (origins ≥ 2026-07-01) was **not loaded, predicted, evaluated or
  inspected**.
- **Not done:** no LightGBM, no weather, no API or dashboard.

## Phase 3 closeout (done first)

- **Owner CLAUDE.md edit:** committed as `144ed3a` (additive working protocol;
  no secrets; no weakened controls). The tree was then clean.
- **Canonical rebuild:** `phase3_features_20261016_clean` (code `f35ff79`,
  clean). It is **logically identical** to r2: same schemas without metadata,
  exactly equal frames and equal logical hashes. It is **not byte-identical**,
  because each Parquet file embeds `nem_forecast.run_id`. r2 is retained as
  superseded evidence. Details are in `phase_3.md` → Closeout.
- **Tag:** `feature-contract-v1` (annotated, object `c68b52e`) on closeout
  commit `e0a9895`. It was confirmed absent locally and remotely first, and
  was pushed.

## Protocol frozen before any metric

`configs/backtest.yaml` plus decisions D-21…D-29 were committed as `df088d0`
before any backtest code existed. Key points:

| Item | Frozen value |
|---|---|
| Profile / features | `primary_operational`, `phase3_features_20261016_clean` (`feature-contract-v1`) |
| Primary / secondary metric | MAE (MW) of calibrated p50 / aggregate WAPE = Σ\|f−y\|/Σy |
| Folds | 12 monthly folds of origins, 2025-07 … 2026-06, all 24 horizons of an origin together |
| Maturity rule | For fold start `F`, outcomes only from origins `≤ F − 2 d` (all 24 target endpoints `≤ F − 1 h`) |
| Calibration | 180 most recent matured origins; empirical additive residual quantiles per model × horizon; ≥ 150 samples; numpy linear |
| Hybrid weight | Grid 0.00…1.00 (step 0.05); MAE on h 1–23 using matured origins **before** the calibration window (nested, expanding); ties to the smallest `w`; h 24 = flagged previous-week fallback |
| Slices | Civil evening 16–20 (`[16:00, 21:00)`); high demand ≥ training-only P90; seasons by civil month; holiday > weekend > weekday |
| Bootstrap | Market-date blocks, 2,000 replicates, seed 20261016, 95% percentile CI |
| Holdout guard | Config validator rejects dev origins ≥ 2026-07-01. Parquet read filter `origin_utc < 2026-06-30 14:00 UTC`; runtime `HoldoutAccessError`. `evaluate-holdout` refuses without `--owner-approved-final-evaluation` and reads nothing in Phase 4 |

Fold 1 example: validation 2025-07-01…07-31; last matured origin 2025-06-29
(origin 2025-06-30 is excluded because its h = 24 outcome is not matured at
2025-06-30 23:00); calibration 2025-01-01…2025-06-29; weight fit ≤ 2024-12-31.

## Implementation

| Item | Location |
|---|---|
| Forecaster interface, allowlists, baselines | `src/nem_forecast/models/baselines.py` |
| Folds, maturity, holdout guard | `src/nem_forecast/evaluation/folds.py` |
| Residual calibration | `src/nem_forecast/evaluation/calibration.py` |
| Metrics, coverage accounting | `src/nem_forecast/evaluation/metrics.py` |
| Paired daily-block bootstrap | `src/nem_forecast/evaluation/bootstrap.py` |
| Pipeline (prediction → evaluation layer) | `src/nem_forecast/evaluation/backtest.py`; CLI `backtest-dev` |
| Reports and plots | `src/nem_forecast/evaluation/report.py` |

**Information constraints.** Each baseline receives only its allowlisted
columns plus the keys `origin_utc` and `horizon`. Prediction raises
`InputAllowlistError` if anything else is present. Actuals and slice attributes
are joined only after the predictions exist. A test confirms that changing
future targets and an unapproved feature for validation origins leaves every
prediction unchanged.

## Commands and outcomes

| Step | Command | Outcome |
|---|---|---|
| Phase 3 clean rebuild | SLURM 33158036 (commit `f35ff79`, dirty 0) | Gate passed (192 offline, 5 integration); build, validate (9/9), compare → logically identical |
| Freeze | commit `df088d0` | Config + decisions + spec only |
| Code | commit `d72d939` | — |
| Development backtest | SLURM **33158750** (`scratch/gate.sbatch`, commit `d72d939`, dirty 0): full gate + `backtest-dev --run-id phase4_backtest_20261016_dev` | COMPLETED 0:0; results below |

Gate results (SLURM 33158750):

| Check | Outcome |
|---|---|
| `pip check` | No broken requirements |
| `ruff check` | All checks passed |
| `ruff format --check` | 62 files already formatted |
| `mypy` (strict) | No issues (37 files) |
| `pytest` (offline) | **208 passed**, coverage 90% |
| `pytest -m integration` | **6 passed** (incl. real-data fold 1) |
| `backtest-dev` | 35,040 predictions, 365 validation origins, max origin 2026-06-30, `holdout_rows_loaded: 0`; 2.4 s |

## Results (development folds only)

### Coverage: missing forecasts cannot improve rankings

| Model | Expected rows | Predicted rows | Row coverage | Origins with all 24 h | Missing |
|---|---|---|---|---|---|
| `day_week_hybrid` | 8,760 | 8,760 | 1.000 | 365 / 365 | — |
| `previous_week` | 8,760 | 8,760 | 1.000 | 365 / 365 | — |
| `same_weekday_4w` | 8,760 | 8,760 | 1.000 | 365 / 365 | — |
| `previous_day` | 8,760 | 8,395 | 0.958 | **0 / 365** | `lag_24h_unavailable` × 365 (all h = 24) |

### Full-horizon operational comparison (all 24 horizons; identical 8,760 rows)

| Model | MAE (MW) | RMSE | WAPE | Bias (f−y) | Evening MAE | High-demand MAE | Pinball p10 / p50 / p90 | p10–p90 coverage | Mean width | Crossing |
|---|---|---|---|---|---|---|---|---|---|---|
| **day_week_hybrid** (w = 0.60) | **459.7** | 674.3 | **0.0932** | −21.2 | 511.6 | 605.8 | 104.7 / 229.9 / 120.1 | 0.791 | 1,475 | 0 |
| same_weekday_4w | 511.6 | 731.6 | 0.1037 | −21.7 | 599.1 | 708.9 | 111.6 / 255.8 / 132.5 | 0.789 | 1,580 | 0 |
| previous_week | 592.0 | 886.1 | 0.1200 | +19.1 | 688.5 | 727.1 | 146.8 / 296.0 / 152.5 | 0.793 | 1,864 | 0 |

### Common-row comparison including `previous_day` (identical 8,395 rows, h 1–23)

| Model | MAE (MW) | WAPE | Bias | p10–p90 coverage | Mean width |
|---|---|---|---|---|---|
| day_week_hybrid | 465.7 | 0.0945 | −22.3 | 0.791 | 1,495 |
| same_weekday_4w | 521.6 | 0.1059 | −22.9 | 0.789 | 1,612 |
| previous_day | 525.4 | 0.1066 | −8.8 | 0.795 | 1,675 |
| previous_week | 603.7 | 0.1225 | +19.9 | 0.793 | 1,901 |

### Paired daily-block bootstrap (MAE A − B, MW; 365 dates; 2,000 replicates; seed 20261016)

| A − B | Rows | Difference | 95% CI |
|---|---|---|---|
| hybrid − previous_week | full 8,760 | −132.3 | [−159.4, −104.6] |
| hybrid − same_weekday_4w | full 8,760 | −51.8 | [−78.2, −25.5] |
| previous_week − same_weekday_4w | full 8,760 | +80.4 | [+52.9, +109.9] |
| hybrid − previous_day | common 8,395 | −59.8 | [−78.6, −40.7] |
| same_weekday_4w − previous_day | common 8,395 | −3.8 | [−40.4, +31.1] (no clear difference) |
| previous_week − previous_day | common 8,395 | +78.2 | [+35.6, +120.2] |

### Fitted parameters and calibration

- **Hybrid weight:** 0.60 in all 12 folds. Fit windows expand from 2022-01-01
  to 2024-12-31 … 2025-12-01. The first usable weight-fit row is 2022-01-08,
  because `lag_168h` needs history; 25,047 → 32,752 rows.
- **Residual samples:** 180 per model × horizon × fold. The exception is
  `previous_day` at h = 24, which has 0 samples, is not calibrated, and
  remains unavailable.
- **Quantile crossing:** 0 in all models. This follows from per-horizon offsets
  being ordered (p10 ≤ p50 ≤ p90).
- **High-demand thresholds:** training-only P90 per fold, 6,177–6,275 MW.

## Answer to the review question

**Honest under the same information constraints.**
- Every baseline uses only Phase 3 features under `primary_operational` (data
  ending ≤ origin − 1 h).
- Weights and residuals come only from matured outcomes before each fold; the
  weight and residual windows are disjoint.
- Allowlists prevent target or other-feature access.
- A perturbation test shows future targets cannot change predictions.
- The holdout was never loaded.

**Comparable rows.**
- Rankings are reported on identical rows only: 8,760 full-horizon rows, and
  8,395 common rows when `previous_day` is included.
- Coverage tables expose `previous_day`'s structural absence at h = 24 (0 of
  365 complete origins), so it is never ranked on an easier subset.

**Calibrated: marginally yes, conditionally no.**
- Pooled p10–p90 coverage is 0.789–0.795 against a nominal 0.80. However,
  calibration from a trailing 180-day window lags the seasons:

  | Slice (hybrid, common rows) | Coverage |
  |---|---|
  | Summer | 0.665 (66.5%) |
  | Autumn | 0.920 |
  | Holidays | 0.686 (68.6%) |
  | High-demand hours | 0.759 |
  | Fold 7 (2026-01) | 0.587 (58.7%) |
  | Folds 10–11 | 0.92–0.93 |

- Intervals are therefore too narrow in summer and on holidays, and too wide
  in the shoulder seasons.
- This is a limitation of the frozen baseline method, reported rather than
  tuned away. A candidate improvement for later phases is season-matched
  calibration; it is not applied here.

## Selected slices (common rows; full tables in `slices.md`)

| Slice | Hybrid MAE | previous_day MAE | same_weekday_4w MAE |
|---|---|---|---|
| Summer / autumn / winter / spring | 648.9 / 353.7 / 430.5 / 433.1 | 698.1 / 393.8 / 512.4 / 500.9 | 674.6 / 438.6 / 462.2 / 514.4 |
| Weekday / weekend / holiday | 463.4 / 459.6 / **555.7** | 512.1 / 559.6 / **517.1** | 523.8 / 492.2 / 708.8 |
| Civil evening 16–21 / other | 511.6 / 452.9 | 562.4 / 515.1 | 599.1 / 500.1 |
| High demand / other | 605.0 / 443.2 | 658.4 / 504.0 | 708.0 / 491.6 |

On holidays `previous_day` beats the hybrid (517 vs 556 MW, n = 299). These
are development results, not claims about future performance.

## Outputs (git-ignored; `results/runs/phase4_backtest_20261016_dev/`)

| Kind | Files |
|---|---|
| Configuration | `backtest_config.resolved.json` |
| Folds and fitted parameters | `folds.json` (folds + thresholds), `fitted_parameters.json` |
| Residual calibration | `residual_quantiles.{parquet,json,md}` |
| Predictions and evaluation | `predictions.parquet` (35,040 rows, no actuals), `evaluation_rows.parquet` (actuals + slices) |
| Metrics and slices | `metrics.{json,md}`, `slices.md` |
| Plots | `plots/{mae_by_horizon,coverage_by_horizon,mae_by_fold}.png` (small multiples) |
| Manifest | `run_manifest.json` (SHA-256 `140bcab3e18fa9a9503fd3e642e407b7b8ffbcd9d1d5e8ede8e265d9f70471c8`) |

The manifest records:
- code `d72d939` (clean);
- the feature-contract tag object `c68b52e` and commit `e0a9895`;
- the feature-run manifest hash `e407910b…`;
- the verified feature partition hashes;
- config hashes;
- the holdout guard (`max_origin_loaded` 2026-06-30, `holdout_rows_loaded` 0);
- every output hash.

## Claim status

| Claim | Basis |
|---|---|
| No holdout origin was loaded, predicted or evaluated | Fresh run (manifest guard fields) + guard tests |
| Predictions invariant to future targets and unapproved columns | Offline tests |
| Hybrid best on full-horizon and common rows | Fresh development run; paired bootstrap CIs exclude 0 |
| Marginal calibration ≈ nominal; seasonal miscalibration | Fresh development run |
| Generalisation to the holdout or future | **Not established** (holdout untouched by design) |

## Closeout (2026-10-16, after owner approval)

- **Frozen development benchmark:** `day_week_hybrid`, weight 0.60, run
  `phase4_backtest_20261016_dev` (code `d72d939`; manifest `140bcab3…`).
  - The values are recorded in
    `docs/benchmarks/phase4_development_benchmark.json`.
  - The README benchmark table and statements are verified against that
    record by `tests/test_docs.py`.
  - An integration test checks that the record matches the local run's
    `metrics.json` and manifest hash.
- **Calibration results preserved.** The trailing-180-day calibration results
  and finding D-32 are unchanged. No baseline prediction was changed or
  rerun.
- **Rounding correction.** Summer coverage is 0.6647 (66.5%). The earlier
  wording “summer 0.67” rounded the three-decimal 0.665 a second time. The
  decision log now says 0.665, and the README states 66.5%, 58.7% (January
  2026) and 68.6% (holidays). The pushed commit message of `d54ade7` still
  says 0.67.
- **Repository-domain correction** (D-34): see `docs/decisions.md`.
- **Holdout:** never loaded or evaluated. The next protocol (LightGBM or
  calibration experiments) awaits owner approval.
