# Phase 6 report: LightGBM development run (development folds only)

Date: 2026-10-16 · Status: **complete; awaiting owner review.**

- **Holdout:** the final holdout (origins ≥ 2026-07-01) was **not loaded,
  predicted or evaluated**, and `evaluate-holdout` was not executed.
- **No selection:** no model has been selected for holdout evaluation.
- **README:** its performance claims are deliberately unchanged until this
  review.

## Pre-work (owner instructions 1–3)

| Step | Outcome |
|---|---|
| Domain correction | D-34 was rewritten without reproducing the unrelated terms. `tests/test_domain_hygiene.py` now has **zero exceptions**: patterns embed a one-character class, so the test cannot match its own source, and case-sensitive word boundaries exclude hash digests structurally (no hash allowlisted). The historical commit `144ed3a` is unchanged. Commit `3268ee7` (gate: 213 offline, 7 integration, 9 docs/hygiene passed) |
| Benchmark tag | `baseline-benchmark-v1` was confirmed absent locally and remotely, then created as an annotated tag (object `309e43c`) on `3268ee7` and pushed. That commit contains `configs/backtest.yaml`, the benchmark record, the approved Phase 4 report, the README and the NEM-only CLAUDE.md |
| Protocol freeze | `configs/lightgbm.yaml` plus D-35…D-41 were committed as `24e9a28` **before LightGBM was installed** (verified: `ModuleNotFoundError`). LightGBM 4.7.0 was installed afterwards |

## Protocol (frozen; summary of D-35…D-41)

| Item | Frozen value |
|---|---|
| Inherited | `primary_operational`, `feature-contract-v1`, 12 outer monthly folds 2025-07 … 2026-06, holdout 2026-07-01 … 09-30, MAE primary, aggregate WAPE secondary, maturity rule origin ≤ F − 2 d, slices, identical-row comparisons, market-date bootstrap (seed 20261016, 2,000 replicates) |
| Holdout guard (3 layers) | Config validator; Parquet read filter; runtime `HoldoutAccessError` on loaded and predicted origins |
| Features | 32-column positive allowlist (11 historical, `lag_24h__available`, `horizon`, 11 market-calendar, 8 civil-calendar). `lag_24h` stays NaN at h = 24 |
| Nested chronology | Tuning T = 90 origins before calibration window C (180 origins); inner training ≤ T_start − 2 d; calibration model ≤ C_start − 2 d; final model ≤ F − 2 d |
| Search | 12 candidates (num_leaves {15, 31, 63} × min_data_in_leaf {50, 200} × lambda_l2 {0, 10}); learning rate 0.05; ≤ 2,000 rounds; early stopping after 100 rounds; deterministic, seed 20261016, 4 threads |
| Selection | p50 configuration by inner MAE; p10/p90 use the same structure with their own early-stopped iterations; refits use the selected iterations unchanged |
| Calibration | Quantile-specific additive `p_τ = raw_τ + Q_τ(y − raw_τ)` per horizon. Trailing: window C. Season-matched: same-season matured origins in `[F − 730 d, F − 2 d]`, ≤ 270 most recent, ≥ 150, otherwise trailing fallback. All residuals out-of-sample |
| Ranking | Point: MAE. Uncertainty: **mean pinball loss of p10/p50/p90** (never coverage alone) |

## Commands and outcomes

SLURM **33164750** (`scratch/gate.sbatch`, 4 CPUs; commit `eb0a734`, `dirty=0`;
COMPLETED 0:0, 20 min 42 s):

| Check | Outcome |
|---|---|
| `pip check` | No broken requirements |
| `ruff check` | All checks passed |
| `ruff format --check` | 67 files already formatted |
| `mypy` (strict) | No issues (40 files) |
| `pytest` (offline) | **227 passed**, coverage 87% |
| `pytest -m integration` | **8 passed** (incl. real-data Phase 6 inputs; benchmark hashes verified) |
| `lightgbm-dev --run-id phase6_lightgbm_20261016_dev` | 8,760 rows per candidate, max origin 2026-06-30; 1,179 s; max RSS 0.34 GB |

Run manifest SHA-256 `a81ae775c5fd5dd3556d8afb1054df5f9533d552d01d5fe08ac4ef98f0acbd06`. It records:
- code `eb0a734` (clean);
- contracts: `data-contract-v1` → `e5c84a9`, `feature-contract-v1` → `e0a9895`,
  `baseline-benchmark-v1` → `3268ee7`;
- the verified Phase 4 benchmark run;
- config hashes, LightGBM 4.7.0, and every output hash;
- the guard fields: `holdout_rows_loaded` 0, `evaluate_holdout_executed` false.

## Conditional calibration first: p10–p90 coverage (nominal 0.80; identical 8,760 rows)

| Slice | Hybrid frozen | Hybrid season-cal. | LGB raw | LGB trailing-cal. | LGB season-cal. |
|---|---|---|---|---|---|
| Summer | 0.664 | 0.803 | 0.714 | 0.797 | 0.843 |
| Autumn | 0.916 | 0.809 | 0.743 | 0.815 | 0.819 |
| Winter | 0.816 | 0.761 | 0.674 | 0.762 | 0.765 |
| Spring | 0.765 | 0.774 | 0.722 | 0.750 | 0.801 |
| Holiday | 0.690 | 0.633 | 0.725 | 0.818 | 0.863 |
| High demand (training P90) | 0.758 | 0.720 | 0.627 | 0.713 | 0.745 |
| Worst fold | 0.587 (fold 7) | 0.718 (fold 2) | 0.610 (fold 12) | 0.689 (fold 12) | 0.713 (fold 12) |
| Best fold | 0.927 (fold 11) | 0.869 (fold 8) | 0.794 (fold 9) | 0.868 (fold 9) | 0.868 (fold 6) |
| **Pooled** | 0.791 | 0.787 | 0.713 | 0.781 | 0.807 |

**No candidate is conditionally calibrated.**
- **Raw LightGBM** under-covers in every season (0.67–0.74).
- **Both calibrations** bring the pooled value near 0.80 but leave large
  conditional gaps:
  - high-demand hours: 0.71–0.75;
  - folds 11–12 (May–June 2026): 0.69–0.76;
  - LGB season-calibrated over-covers summer (0.84) and holidays (0.86).
- **Season calibration of the hybrid** fixes its summer under-coverage
  (0.66 → 0.80) but worsens holidays (0.69 → 0.63) and winter (0.82 → 0.76).

## Full-horizon comparison (identical 8,760 rows)

| Candidate | MAE (MW) | RMSE | WAPE | Bias | Mean pinball | p10 / p50 / p90 pinball | Coverage | Width | Crossing (rate / mean MW) | Evening MAE | High-demand MAE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| day_week_hybrid_frozen | 459.7 | 674.3 | 9.32% | −21.2 | 151.6 | 104.7 / 229.9 / 120.1 | 0.791 | 1,475 | 0 / 0 | 511.6 | 605.8 |
| day_week_hybrid_season_calibrated | 457.2 | 671.7 | 9.27% | −21.8 | 145.8 | 98.2 / 228.6 / 110.6 | 0.787 | 1,455 | 0 / 0 | 504.7 | 611.3 |
| lightgbm_raw | 363.2 | 543.2 | 7.36% | −31.7 | 116.8 | 76.8 / 181.6 / 91.9 | 0.713 | 1,005 | 0.94% / 0.49 | 420.8 | 521.7 |
| lightgbm_trailing_calibrated | **362.8** | 546.7 | 7.35% | −34.5 | 116.0 | 76.0 / 181.4 / 90.8 | 0.781 | 1,167 | 0.87% / 0.41 | 419.7 | 524.1 |
| lightgbm_season_calibrated | 363.4 | 552.1 | 7.37% | **−76.3** | **115.3** | 74.0 / 181.7 / 90.1 | 0.807 | 1,232 | 0.58% / 0.24 | 420.7 | 533.0 |

Prediction coverage: 8,760 of 8,760 rows and 365 of 365 complete origins for
every candidate.

### Paired market-date bootstrap (365 dates; 2,000 replicates; seed 20261016)

| A − B | MAE difference (95% CI) | Mean-pinball difference vs frozen hybrid (95% CI) |
|---|---|---|
| hybrid_frozen − lightgbm_raw | +96.5 [+78.9, +114.7] | — |
| hybrid_frozen − lightgbm_trailing | +97.0 [+79.1, +115.4] | — |
| hybrid_frozen − lightgbm_season | +96.3 [+78.2, +114.9] | — |
| lightgbm_raw − hybrid_frozen | — | −34.8 [−40.8, −29.2] |
| lightgbm_trailing − hybrid_frozen | — | −35.5 [−41.5, −29.9] |
| lightgbm_season − hybrid_frozen | — | −36.3 [−42.6, −30.6] |
| hybrid_season − hybrid_frozen | −2.6 [−6.3, +1.2] (A = frozen: +2.55 [−1.17, +6.30]) | −5.8 [−7.9, −3.6] |
| LGB raw vs trailing vs season (MAE) | all three pairwise CIs include 0 (e.g. season − trailing +0.63 [−3.4, +4.4]) | — |

On development data, LightGBM reduces MAE by about 96–97 MW (≈ 21%) and mean
pinball by about 35–36 MW against the frozen hybrid. Both intervals exclude
zero. The three LightGBM variants have indistinguishable MAE.

### Quantile crossing

- Raw LightGBM crosses on 0.94% of rows (mean magnitude 0.49 MW).
- Calibration reduced crossing (trailing 0.87%, season 0.58%) and introduced
  none on balance.
- Candidates are evaluated **uncorrected**.
- Sorting p10 ≤ p50 ≤ p90 (a reported variant only) changes LightGBM MAE by
  ≤ 0.14 MW and mean pinball by ≤ 0.07.

## Tuning and calibration records

- **Selected structures:** num_leaves 15 (6 folds) or 31 (6 folds);
  min_data_in_leaf 50 (7) or 200 (5); lambda_l2 0 (5) or 10 (7).
- **Iterations:** p50 261–1,018, p10 72–376, p90 99–360.
- **Other records:** all 12 candidates per fold, with inner MAE and best
  iteration, are in `folds_and_tuning.json`.
- **Season-matched calibration:** 6 archive months per fold, 179–184 samples
  per horizon, **0 fallbacks** (864 of 864 cells used season residuals) for
  both LightGBM and the hybrid.
- **Trailing calibration:** 180 samples per horizon.

## Selected slices (identical rows)

| Slice | Hybrid frozen MAE | LGB raw MAE | LGB trailing MAE | LGB season MAE |
|---|---|---|---|---|
| Summer / autumn / winter / spring | 639.8 / 350.6 / 425.0 / 427.1 | 471.4 / 275.1 / 348.1 / 360.5 | 469.2 / 278.1 / 347.6 / 358.5 | 469.2 / 278.7 / 346.8 / 361.2 |
| Holiday / weekday / weekend | 546.8 / 457.6 / 453.8 | 315.1 / 362.5 / 371.3 | 304.6 / 363.5 / 368.4 | 294.2 / 364.7 / 369.0 |
| Civil evening 16–21 / other | 511.6 / 446.1 | 420.8 / 348.1 | 419.7 / 347.8 | 420.7 / 348.3 |
| High demand / other | 605.8 / 437.3 | 521.7 / 338.9 | 524.1 / 338.0 | 533.0 / 337.4 |

Full fold, horizon, market-hour and day-type tables are in `slices.md`.

## Feature importance (descriptive, not causal)

Mean gain share of the final p50 models:

| Feature | Share |
|---|---|
| `same_hour_mean_7d` | 0.258 |
| `same_hour_same_weekday_mean_4w` | 0.198 |
| `lag_24h` | 0.125 |
| `recent_24h_max` | 0.062 |
| `market_weekday` | 0.038 |
| `recent_24h_std` | 0.037 |

Gain reflects how the trees split. It does not measure effects on demand.

## Runtime

- Total 1,179 s.
- Per fold:
  - tuning 13–109 s;
  - trailing calibration 1–5 s;
  - season archive 8–237 s;
  - final fit 1.5–3.7 s;
  - outer prediction ≤ 0.1 s.

  The variation reflects shared-node contention, not the data.

## Outputs (git-ignored; `results/runs/phase6_lightgbm_20261016_dev/`)

| Kind | Files |
|---|---|
| Protocol and allowlist | `protocol.resolved.json`, `feature_allowlist.json` |
| Folds and tuning | `folds_and_tuning.json`: outer/inner windows, all candidates, selections, iterations, seeds (via protocol), archive months, row counts, timings, importance, fallback counts |
| Predictions | `predictions.parquet` (raw and calibrated p10/p50/p90 for all five candidates), `evaluation_rows.parquet` |
| Calibration | `calibration_offsets.parquet` (samples, offsets, sources/fallbacks per fold × horizon × quantile) |
| Metrics | `metrics.{json,md}`, `slices.md` |
| Plots | `plots/{mae_by_horizon,coverage_by_fold,mean_pinball_by_fold}.png` |
| Models | `models/` (36 final boosters as text) |
| Manifest | `run_manifest.json` |

## Limitations

- **Hyperparameters in calibration archives:** they are selected at fold time
  and reused for the season-calibration archive months (D-40). No outer-fold
  outcomes are used.
- **Season-calibration bias:** it shifts the median for LightGBM, giving a
  bias of −76 MW against −32 MW raw without changing MAE. Earlier seasons'
  median residuals do not transfer cleanly.
- **Late folds:** May–June 2026 under-coverage affects all LightGBM variants.
  The cause is not established.
- **Scope:** these are development results only. Holdout and future
  performance are not established.

## Claim status

| Claim | Basis |
|---|---|
| Holdout not loaded or evaluated | Fresh run guard fields + guard tests |
| Predictions invariant to future targets, audit fields, extra columns and column order | Offline tests |
| LightGBM lower MAE and mean pinball than the frozen hybrid on development rows | Fresh run; paired bootstrap CIs exclude 0 |
| Any candidate conditionally calibrated | **Not established**: conditional coverage gaps shown above |
| Generalisation to the holdout or future | **Not established** |
