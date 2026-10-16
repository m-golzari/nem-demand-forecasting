# Project checkpoint (compact ledger)

Updated 2026-10-16. The authoritative detail is in the phase reports and run
manifests.

## Objective and scope
- A next-24-hour forecast of the **hourly mean of AEMO dispatch `TOTALDEMAND`
  estimates for VIC1** (MW), from a daily 00:00 NEM-market-time origin.
- Outputs: p10/p50/p90.
- This is a portfolio prototype.

## Current phase
- **Phase 3 complete; awaiting owner review.**
- Next: Phase 4 (baselines and rolling backtest). Not started; needs owner
  approval.

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
- Phase 3 run `phase3_features_20261016_r2` (code `cea47ae`; dirty only by
  the owner's `CLAUDE.md`):
  - profiles `primary_operational` (cutoff `O − 1 h`) and
    `sensitivity_zero_lag` (cutoff `O`; sensitivity only);
  - manifest SHA-256 `4cb6a3a4…`.
- Decisions: A-01…A-08, D-01…D-20 in `docs/decisions.md`. Key ones:
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
