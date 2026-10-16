# Project checkpoint (compact ledger)

Updated 2026-10-16. The authoritative detail is in the phase reports and run
manifests.

## Objective and scope
- A next-24-hour forecast of the **hourly mean of AEMO dispatch `TOTALDEMAND`
  estimates for VIC1** (MW), from a daily 00:00 NEM-market-time origin.
- Outputs: p10/p50/p90.
- This is a portfolio prototype.

## Current phase
- **Phase 3 approved (2026-10-16).** Closeout: clean rebuild designated
  canonical; tag `feature-contract-v1`.
- **Phase 4 approved (2026-10-16); frozen development benchmark = day/week hybrid, w = 0.60 (D-33).** Run
  `phase4_backtest_20261016_dev` (code `d72d939`, clean; manifest
  `140bcab3…`). The protocol was frozen in `configs/backtest.yaml` (commit
  `df088d0`) before any metric. The final holdout (origins ≥ 2026-07-01) has
  never been loaded, and evaluating it needs explicit owner approval.
- Development headline (identical rows):
  - hybrid (w = 0.60) MAE 459.7 MW (full horizon);
  - same_weekday_4w 511.6;
  - previous_week 592.0;
  - on common h 1–23 rows, previous_day 525.4 vs hybrid 465.7.
- p10–p90 coverage is 0.791 pooled but seasonally miscalibrated (D-32: summer 0.665, January 2026 0.587, holidays 0.686).
- Phase 4 frozen benchmark tagged `baseline-benchmark-v1` (`3268ee7`). Domain-hygiene test is exception-free.
- **Phase 6 LightGBM development run complete; awaiting owner review.** Protocol frozen in `configs/lightgbm.yaml` (`24e9a28`, before LightGBM was installed). Run `phase6_lightgbm_20261016_dev` (code `eb0a734`, clean; manifest `a81ae775…`).
  - Development MAE: hybrid 459.7; LightGBM raw 363.2, trailing 362.8, season 363.4 MW.
  - Mean pinball: hybrid 151.6; LightGBM 116.8 / 116.0 / 115.3.
  - No candidate is conditionally calibrated (D-42).
  - No model selected. Holdout locked; `evaluate-holdout` not executed.
  - README performance claims not updated pending review.

## Frozen decisions and identities
- Source snapshot `DISPATCHREGIONSUM_2022-01_2026-09_retrieved20261014`:
  - 57 archives;
  - lock `configs/data_snapshots/…lock.json`, SHA-256 `25ebc30a…6180`.
- Tag `data-contract-v1` (annotated, object `d5a93768`) on commit `e5c84a9`:
  source, time, intervention, hourly-target and quality contracts.
- Phase 2 run `phase2_vic1_hourly_20261014_full` (code `468c0af`):
  - 41,616 of 41,616 complete hours, 2022-01-01 00:00 to 2026-09-30 23:00
    (+10:00);
  - manifest SHA-256 `da3ed96c…`.
- Phase 3 **canonical** run `phase3_features_20261016_clean` (code `f35ff79`,
  clean; manifest `e407910b…`). It is logically identical to the superseded
  r2 run (code `cea47ae`). Details:
  - profiles `primary_operational` (cutoff `O − 1 h`) and
    `sensitivity_zero_lag` (cutoff `O`; sensitivity only);
  - manifest SHA-256 `4cb6a3a4…`.
- Decisions: A-01…A-08, D-01…D-42 in `docs/decisions.md`. Key ones:
  - D-06: physical run selected in intervention intervals;
  - D-07: 1 h availability buffer (a modelling assumption);
  - D-16: structural `lag_24h` null at h = 24 under the primary profile.

## Key results (data pipeline only; no model results)
- 0 incomplete hours; 297 intervention-affected hours.
- Phase 3: 0 used values with a source endpoint after the cutoff, and 0
  disagreements with the physically truncated reference across 457,776 values
  per profile.
- DuckDB: 9 of 9 checks.
- Initial-ML eligible rows: 40,944 per profile; first fully eligible origin
  2022-01-29.

## Open issues and limitations
- Snapshot values are last-changed values (`LASTCHANGED` rewrites up to
  ~63 h in 2022–2024). Real-time availability is not established.
- Intervention flag documentation (v2.3, 2024) is applied retrospectively.
- Holiday calendar from `holidays` 0.106. Advance knowledge of one-off
  holidays (e.g. 2022-09-22) is not verified.
- Open questions: O-06 (calendar time basis, partly addressed: both market and
  civil fields exist), O-07 (metric, folds, holdout), O-08 (baseline
  quantiles).

## Commands
```bash
.venv/bin/nem-forecast build-hourly --run-id <id>       && .venv/bin/nem-forecast validate-data --run-id <id>
.venv/bin/nem-forecast build-features --run-id <id>     && .venv/bin/nem-forecast validate-features --run-id <id>
sbatch scratch/gate.sbatch "<extra command>" ...         # local SLURM gate (git-ignored script)
```

## Key paths (git-ignored outputs)
- `data/processed/features/<run>/profile=<p>/year=Y/part-0.parquet`
- `data/interim/feature_audit/<run>/…`
- `results/runs/<run>/`
