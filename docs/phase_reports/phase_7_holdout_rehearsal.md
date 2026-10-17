# Final evaluator rehearsal (rehearsal only)

Date: 2026-10-17 · Status: **rehearsal passed after the D-49 threshold
correction.** It is not a holdout result. It updates no benchmark and no
model selection.

The rehearsal `phase7_rehearsal_20261017b` supersedes `phase7_rehearsal_20261017`
(commit `e749861`). That earlier run recomputed the high-demand threshold. To
do so it opened 36,456 pre-window historical targets, which is inconsistent
with the frozen protocol. Commit `add00d9` (D-49) removed that behaviour.

## What was rehearsed

The command was `nem-forecast holdout-rehearsal --run-id phase7_rehearsal_20261017b`
(SLURM 33277409). It ran from clean commit `add00d9` (dirty 0) under protocol
`final-holdout-evaluation-v1` (`configs/final_evaluation.yaml`, D-47; frozen in
commit `dec5793` and unchanged).

- **Window:** development origins 2026-03-01 … 2026-05-31, prespecified in the
  protocol. This gives 92 origins and 2,208 rows, the same shape as the holdout.
- **Artefact:** a byte-identical copy of `final_artefact_20261017`. Every file
  hash was compared after copying, and all artefact checks were rerun on the
  copy.
- **Inputs:** a copy of the canonical Phase 3 rows with origin before
  2026-07-01, applied as a Parquet read filter. The copy holds 39,408 rows,
  max origin 2026-06-30, and **0 holdout rows** (SHA-256 `be098b62…`). The
  rehearsal evaluator read only this copy.
- **High-demand threshold:** the frozen scalar **6,243.07 MW**
  (6,243.067666666667), read from the protocol. It was verified against the
  pinned provenance record
  `docs/benchmarks/final_evaluation_high_demand_threshold.json` (SHA-256
  `b39b14a6…f42f586c`): 8 of 8 checks passed. It was not recomputed.
- **In-sample caveat:** the final models were trained on origins up to
  2026-06-29 and calibrated on 2026-01-01 … 06-29, so the rehearsal origins
  are in-sample. **Rehearsal metric values are pipeline checks, not
  performance evidence.** They stay in the git-ignored run directory.

## Outcomes

| Check | Outcome |
|---|---|
| Artefact binding (manifest, 3 model hashes, offset table + inline records, fallback file and weight, allowlist order + hash, LightGBM 4.7.0, 4 tags in manifest and repository, holdout boundary, unchanged configs, feature-run identity) | All checks passed |
| Prediction rows | **4,416 total: 2,208 candidate (`lightgbm_trailing_calibrated`) and 2,208 fallback (`day_week_hybrid_frozen`)**. Keys + 32 allowlisted columns + `lag_168h__available` only; no target column |
| Seal | `predictions.parquet` written atomically, read-only, exact reload. SHA-256 `3a5cee70…`; sealed 2026-10-17T09:51:59.435692Z |
| Target access | **Predictions were sealed before target access.** Targets were first opened at 09:51:59.438207Z, and the ledger recorded the access before the read. **Exactly 2,208 rehearsal target rows were opened** (the window only) |
| Historical threshold rows opened | **0** |
| High-demand threshold | **6,243.07 MW** (frozen; `recomputed: false`) |
| Row reconciliation | 2,208 identical rows of 2,208; 92 origins; horizons 1–24; 0 target rows unavailable |
| Quantile crossing (frozen order, not sorted) | Candidate: 2 calibrated rows (16 raw rows); fallback: 0 |
| Bootstrap | Market-date blocks: 92 dates, 2,000 replicates, seed 20261016 (MAE and mean pinball) |
| Repeat after success | Refused (`EvaluationRefusedError`) |
| Overwrite of the output directory | Refused (`FileExistsError`) |
| Real holdout target rows loaded | **0** |

The rehearsal evaluation's pre-execution manifest has SHA-256 `8d387b42…`, and
its run manifest has SHA-256 `f28db706…`.

## Offline tests (synthetic data only)

`tests/test_holdout_evaluation.py` has 27 tests. They use a synthetic
artefact with a synthetic "holdout" starting 2025-07-01, covering 31 origins
and 744 rows, plus a synthetic pinned threshold record. In addition to the
binding, stage-order, row-contract, formula, metric, crossing and
failure-semantics tests already recorded, D-49 adds tests proving that:

- poisoned pre-window target values leave the threshold and every
  high-demand classification identical;
- the threshold equals the frozen configured scalar exactly, in the
  evaluation rows and in the pre-execution and run manifests;
- an altered threshold, a tampered record or a re-pinned record with another
  provenance identity is refused before prediction;
- the evaluator makes exactly two reads: features before the seal, and the
  window's targets after it. It requests no pre-window target row;
- the real threshold equals 6,243.067666666667 and is verified against the
  real record;
- the real holdout and rehearsal windows each expect exactly 2,208 rows;
- preflight cannot decode rows.

The bounded integration test
`test_real_holdout_preflight_binds_frozen_artefact_without_reading_rows`
verifies every binding, including the threshold record, against the real
files with the row reader disabled.
