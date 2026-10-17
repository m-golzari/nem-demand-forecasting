# Pre-holdout report: final artefact for the tagged candidate (owner gate)

Date: 2026-10-17 · Status: **built; awaiting the owner's final-gate decision.**

- **Holdout:** origins 2026-07-01 … 2026-09-30 were **not loaded, predicted
  or evaluated**, and `evaluate-holdout` has not been executed.
- **Candidate:** `lightgbm_trailing_calibrated`, tag `lightgbm-development-v1`
  (object `538c003`, commit `bc580d5`).
- **Fallback and benchmark:** `day_week_hybrid_frozen` (`baseline-benchmark-v1`).

## Artefact `final_artefact_20261017`

Built by `nem-forecast build-final-artefact --run-id final_artefact_20261017`
in SLURM 33273584, from clean commit `bfed6d7` (dirty 0). Pre-evaluation
manifest SHA-256 `58ef2cfb70487ea3720c465a0d69fbc46b44f0544ae7a7c45f87608994267a35`.

The holdout start 2026-07-01 is treated as one more fold start under the
frozen development rules.

| Item | Value |
|---|---|
| Inputs | `phase3_features_20261016_clean` (manifest `e407910b…`), `primary_operational`. 39,408 rows loaded, max origin 2026-06-30, **holdout rows loaded 0** (Parquet filter + guard) |
| Maturity cutoff | 2026-06-30 23:00 market time (holdout start − 1 h) |
| Inner training origins | 2022-01-01 … 2025-10-01 (32,208 eligible rows) |
| Tuning origins | 2025-10-03 … 2025-12-31 (2,160 rows) |
| Calibration-model training | origins 2022-01-01 … 2025-12-30 (34,368 rows) |
| Trailing calibration origins | 2026-01-01 … 2026-06-29 (180 origins, 4,320 rows; 180 samples per horizon × quantile) |
| Final training origins | 2022-01-29 … 2026-06-29 (38,712 eligible rows; latest target hour ends 2026-06-30 00:00 +10:00) |
| Selected structure | candidate 2: num_leaves 15, min_data_in_leaf 200, lambda_l2 0 (inner MAE 374.3 MW) |
| Iterations | p10 344, p50 387, p90 249 |
| Model files (SHA-256) | p10 `1d168e4e…f173d335`, p50 `224c7f6e…a6460ce9a`, p90 `dcb7b3c2…5a7ff0f` |
| Trailing offsets | Median p10 −31.6, p50 +22.1, p90 +123.9 MW (ranges −167…+98, −62…+92, +51…+300) |
| Hybrid fallback | Weight 0.60 (fit origins 2022-01-01 … 2025-12-31). Trailing offsets on 2026-01-01 … 06-29 (180 samples). h24 = flagged previous-week fallback |
| Feature allowlist | The 32 columns in tag order (manifest `feature_allowlist`) |
| Environment | LightGBM 4.7.0; deterministic, seed 20261016, 4 threads |
| Tags recorded | `data-contract-v1` → `e5c84a9`, `feature-contract-v1` → `e0a9895`, `baseline-benchmark-v1` → `3268ee7`, `lightgbm-development-v1` → `bc580d5` |

### Save/load consistency

- Fixture: origins 2026-06-23 … 2026-06-29 (168 pre-holdout rows, no target
  columns).
- The reloaded models reproduce the in-memory raw predictions with a maximum
  difference of **0.0 MW** (frozen tolerance 1e-9 MW).
- The integration test `test_real_final_artefact_reloads_exactly` passed after
  the build.
- Offline tests cover the remaining guarantees:
  - exact reload;
  - model-file tamper detection;
  - rejection of holdout rows;
  - invariance of the models to unmatured outcomes (origin = holdout − 1 d);
  - CLI guards.
- An integration test confirms that the refactored tuning function reproduces
  the recorded development fold-1 selection exactly (structure and all three
  iteration counts).

## Checks (SLURM 33273584)

| Check | Outcome |
|---|---|
| `pip check` | No broken requirements |
| `ruff check` | All checks passed |
| `ruff format --check` | 71 files already formatted |
| `mypy` (strict) | No issues (42 files) |
| `pytest` (offline) | **238 passed** |
| `pytest -m integration` | **9 passed**, 1 skipped (artefact not yet built); then **2 of 2 passed** post-build |

## Proposed one-time holdout command (NOT executed)

```bash
nem-forecast evaluate-holdout --owner-approved-final-evaluation --artefact-run-id final_artefact_20261017
```

- **Current state:** the command refuses without the approval flag. With the
  flag it currently raises “not implemented … nothing was read”. The
  evaluation logic itself has **not been written**, so no holdout code path
  exists that could have read outcomes.
- **On owner approval:** implement it under a separately frozen evaluation
  protocol, using the same metrics, slices, identical-row rules and bootstrap,
  this artefact, and the hybrid fallback. Run it once.
