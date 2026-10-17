# Final evaluator rehearsal (rehearsal only)

Date: 2026-10-17 · Status: **rehearsal passed.** It is not a holdout result.
It updates no benchmark and no model selection.

## What was rehearsed

The command was `nem-forecast holdout-rehearsal --run-id phase7_rehearsal_20261017`
(SLURM 33275008). It ran from clean commit `e749861` (evaluator code, dirty 0)
under protocol `final-holdout-evaluation-v1` (`configs/final_evaluation.yaml`,
D-47; frozen in commit `dec5793`).

- **Window:** development origins 2026-03-01 … 2026-05-31, prespecified in the
  protocol. This gives 92 origins and 2,208 rows, the same shape as the holdout.
- **Artefact:** a byte-identical copy of `final_artefact_20261017`. Every file
  hash was compared after copying, and all artefact checks were rerun on the
  copy.
- **Inputs:** a copy of the canonical Phase 3 rows with origin before
  2026-07-01, applied as a Parquet read filter. The copy holds 39,408 rows,
  max origin 2026-06-30, and **0 holdout rows** (SHA-256 `be098b62…`). The
  rehearsal evaluator read only this copy.
- **In-sample caveat:** the final models were trained on origins up to
  2026-06-29 and calibrated on 2026-01-01 … 06-29, so the rehearsal origins
  are in-sample. **Rehearsal metric values are pipeline checks, not
  performance evidence.** They stay in the git-ignored run directory and are
  not reported here as results.

## Outcomes

| Check | Outcome |
|---|---|
| Artefact binding (manifest, 3 model hashes, offset table + inline records, fallback file and weight, allowlist order + hash, LightGBM 4.7.0, 4 tags in manifest and repository, holdout boundary, unchanged configs, feature-run identity) | All checks passed |
| Prediction stage | Projected keys + 32 allowlisted columns + `lag_168h__available`; no target column. 4,416 predictions (2,208 per model) |
| Seal | `predictions.parquet` written atomically, read-only, exact reload. SHA-256 `3a5cee70…`; sealed 2026-10-17T09:00:40.948576Z |
| Target access | First opened 2026-10-17T09:00:40.951217Z, after the seal, recorded in the ledger before the read. 2,208 window target rows; 36,456 pre-window training targets for the high-demand threshold (origins ≤ 2026-02-27) |
| Row reconciliation | 2,208 identical rows of 2,208; 92 origins; horizons 1–24; 0 target rows unavailable |
| Quantile crossing (frozen order, not sorted) | Candidate: 2 calibrated rows (16 raw rows); hybrid: 0 |
| Bootstrap | Market-date blocks: 92 dates, 2,000 replicates, seed 20261016 (MAE and mean pinball) |
| Seasons observed | autumn (as expected for March–May) |
| Repeat after success | Refused (`EvaluationRefusedError`: a successful evaluation already exists) |
| Overwrite of the output directory | Refused (`FileExistsError`) |
| Holdout target rows loaded | **0** |

The pre-execution manifest of the rehearsal evaluation has SHA-256
`066b0e22…`, and its run manifest has SHA-256 `123baf7a…`.

## Offline tests (synthetic data only)

`tests/test_holdout_evaluation.py` has 23 tests. They use a synthetic
artefact with a synthetic "holdout" starting 2025-07-01, covering 31 origins
and 744 rows. The tests cover:

- the missing owner flag, the missing run ID and a wrong artefact ID;
- tampered model, offset and fallback files, a wrong pinned manifest hash, a
  reversed allowlist and a moved tag;
- a dirty tree, and a rehearsal window that overlaps the holdout;
- no target column in the prediction projection, and every target read
  happening after the seal (spy on the only reader);
- a missing or changed seal;
- missing, duplicate and extra rows;
- no fit, tuning or calibration call (all monkeypatched to fail);
- the exact candidate and fallback formulas, and an exact save/load of the
  predictions;
- tail-miss rates, slice dimensions and a recomputed daily-block bootstrap;
- crossings reported without sorting;
- repeat and overwrite refusal;
- failure manifests before target access (retry stays pristine) and after it
  (disclosure required; the rerun is not pristine);
- a frozen-threshold mismatch.

The bounded integration test
`test_real_holdout_preflight_binds_frozen_artefact_without_reading_rows` runs
every binding against the real files, with the row reader disabled.
