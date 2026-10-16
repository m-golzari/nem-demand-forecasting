# Phase 3 report: point-in-time feature dataset

Date: 2026-10-16 · Status: **complete and approved by the owner (2026-10-16).**
The canonical run and the `feature-contract-v1` tag are recorded in “Closeout”
at the end of this report. No model was
trained or evaluated. There was no target-correlation screening, feature
selection, baseline evaluation, tuning or holdout access.

## Phase 2 closeout (done first)

- **Phase 2 report:** marked approved. It now distinguishes **16
  pipeline-derived quality invariants** (`target/build.py::derived_checks`)
  from **13 independent re-derivation checks**
  (`target/reconcile.py::reconcile_run`).
- **README:** your README was verified against the repository. The incorrect
  `api/` path was fixed to `serving/` and the status was updated.
  `tests/test_docs.py` now fails if the README references a non-existent path,
  link, CLI command or make target.
- **Commit:** `e5c84a9`, after a full offline gate (SLURM 33156011: 167
  passed, 3 integration passed, lint, format and strict mypy clean).
- **Tag:** `data-contract-v1` was confirmed absent locally and remotely, then
  created as an **annotated** tag (object `d5a93768`) on `e5c84a9`. Its
  message holds the source, time, intervention, hourly-target and quality
  contracts. It was pushed and will not be moved.

## Can every historical feature value be shown to be computable from source intervals available by its origin?

The evidence chain is below. "Verified" means checked on the full dataset in
run `phase3_features_20261016_r2`.

1. **Source endpoint per observation.** Each hourly observation carries its
   actual latest five-minute source endpoint, Phase 2's
   `last_source_settlementdate`. That is the maximum raw `SETTLEMENTDATE` of
   its selected records, and it was independently re-derived from the raw
   archives in Phase 2. Verified: for all 41,616 complete hours it equals
   label + 1 h (0 violations).
2. **Cutoff on source endpoints.** A required observation is used only if that
   endpoint is `≤ cutoff = origin − lag`, never by label.
   - Verified: **0** of 454,386 (primary) and 456,264 (zero-lag) used
     feature values have `latest_source_endpoint > cutoff`.
   - The same 0 holds independently in DuckDB (`audit_endpoint_after_cutoff = 0`).
3. **Physical truncation.** An independent per-origin builder
   (`features/reference.py`) receives copies of the observation arrays that
   contain only hours with endpoint `≤ cutoff`. Later data do not exist in
   its input. Verified for **all** 457,776 (row × feature) values per profile:
   - 0 availability disagreements;
   - 0 reason disagreements;
   - 0 latest-endpoint disagreements;
   - maximum absolute value difference **0.0**.
4. **Future perturbation.** An offline test replaces all observations after a
   cutoff with random values and random gaps. Features at that origin are
   unchanged under both profiles.
5. **SQL recomputation.** DuckDB recomputes `lag_24h`, `lag_48h`, `lag_168h`
   and `recent_24h_mean` directly from the Phase 2 Parquet, applying the
   cutoff to parsed source endpoints. Results: 247,483 lag values checked,
   0 availability mismatches, maximum difference 0.0; `recent_24h_mean`
   maximum difference 3.6 × 10⁻¹² (floating-point summation order).
6. **Lineage.** Every used value has complete lineage: represented window,
   latest observation label, latest source endpoint and cutoff (0 incomplete).

Limits of the proof:
- It establishes computability from the **2026 snapshot's** values available
  by the origin under the stated profile.
- It cannot establish that those values were *published* by the origin in
  real time. Archives hold last-changed values, and `LASTCHANGED` rewrites of
  up to ~63 h occurred in 2022–2024.
- The 1 h buffer is a modelling assumption (D-07).
- Calendar features are deterministic. The advance knowledge of one-off
  holidays is noted in the feature registry.

## Implementation

| Item | Location |
|---|---|
| Historical features (vectorised) | `src/nem_forecast/features/history.py` |
| Independent truncated reference | `src/nem_forecast/features/reference.py` |
| Calendars, holidays | `src/nem_forecast/features/calendar.py` (holidays 0.106, AU/VIC) |
| Design table, eligibility, lineage | `src/nem_forecast/features/design.py` |
| Pipeline, reports, manifest | `src/nem_forecast/features/build.py`; CLI `build-features` |
| DuckDB reconciliation | `src/nem_forecast/features/reconcile.py`, `features/sql/features_reconcile.sql`; CLI `validate-features` |
| Config | `configs/features.yaml` (validated; fields fixed to the registered set) |
| Registry | `docs/feature_registry.md` (11 historical + calendar features) |

The structural `lag_24h` case under `primary_operational` at h = 24 is null,
with `__available = false` and reason `after_cutoff`. It is not substituted and
the cutoff is not weakened. Verified: 1,734 of 1,734 h = 24 rows. Under
`sensitivity_zero_lag` it is available for 1,733 of 1,734; the first origin has
insufficient history.

## Commands and outcomes

| Step | Command | Outcome |
|---|---|---|
| Code commits | `2d51fc4` (Phase 3 code), `cea47ae` (fix: manifest listed uncommitted path as `LAUDE.md`; porcelain was stripped; regression test added) | — |
| First full run | SLURM 33156655: gate + `build-features --run-id phase3_features_20261016_full` + `validate-features` | All passed. **Superseded** only because of the uncommitted-path typo in its manifest; kept on disk |
| Final run | SLURM **33156808** (`scratch/gate.sbatch`, commit `cea47ae`) | COMPLETED 0:0, 2 min 7 s |

Final-run gate results (SLURM 33156808):

| Check | Outcome |
|---|---|
| `pip check` | No broken requirements |
| `ruff check` | All checks passed |
| `ruff format --check` | 53 files already formatted |
| `mypy` (strict) | No issues (29 files) |
| `pytest` (offline) | **191 passed**, coverage 92% |
| `pytest -m integration` | **5 passed** (incl. real May–July 2022 features vs truncated reference, both profiles) |
| `build-features --run-id phase3_features_20261016_r2` | 41,616 rows per profile; leakage checks passed; 27 s, 0.70 GB |
| `validate-features --run-id phase3_features_20261016_r2` | **9 of 9** DuckDB checks passed |

The run manifest records `dirty: true` with `uncommitted_paths: ["CLAUDE.md"]`.
That file is your local edit; no code was uncommitted. The eligibility and
schema JSON reports are byte-identical between the first and final runs
(determinism).

## Eligibility and missingness (derived, not hard-coded)

| Flag | primary_operational | sensitivity_zero_lag |
|---|---|---|
| rows | 41,616 | 41,616 |
| target_available | 41,616 | 41,616 |
| eligible_lag24_baseline | 39,859 (h = 24 never; first origin h 1–23 insufficient) | 41,592 |
| eligible_lag168_baseline | 41,448 | 41,448 |
| eligible_initial_ml_features | 40,944 | 40,944 |
| first fully eligible origin (initial ML) | 2022-01-29 (672 h same-weekday lookback) | 2022-01-29 |

Missingness by reason (rows):

| Feature | primary_operational | sensitivity_zero_lag |
|---|---|---|
| `lag_24h` | `after_cutoff` 1,734 (h = 24 only); `insufficient_history` 23 | `insufficient_history` 24 |
| `lag_48h` | `insufficient_history` 48 | 48 |
| `lag_168h` | `insufficient_history` 168 | 168 |
| `recent_24h_*` | `insufficient_history` 48 | 24 |
| `recent_168h_*` | `insufficient_history` 192 | 168 |
| `same_hour_mean_7d` | `insufficient_history` 169 | 168 |
| `same_hour_same_weekday_mean_4w` | `insufficient_history` 672 | 672 |

There was no `missing_source_hour` anywhere (Phase 2 had 0 incomplete hours).
That reason is exercised by offline tests.

By year (primary; rows / lag24 / lag168 / initial ML):

| Year | Rows | lag24 | lag168 | initial ML |
|---|---|---|---|---|
| 2022 | 8,760 | 8,372 | 8,592 | 8,088 |
| 2023 | 8,760 | 8,395 | 8,760 | 8,760 |
| 2024 | 8,784 | 8,418 | 8,784 | 8,784 |
| 2025 | 8,760 | 8,395 | 8,760 | 8,760 |
| 2026 | 6,552 | 6,279 | 6,552 | 6,552 |

Per-horizon counts: `results/runs/phase3_features_20261016_r2/eligibility_report.{json,md}`.

## Feature ranges (primary; MW; from `schema_report.json`)

| Feature | Nulls | Min | Max |
|---|---|---|---|
| `lag_24h` | 1,757 | 1266.76 | 10466.43 |
| `recent_24h_mean` | 48 | 2985.01 | 7329.31 |
| `recent_24h_std` | 48 | 226.28 | 2082.48 |
| `recent_168h_mean` | 192 | 3369.74 | 6707.42 |
| `same_hour_mean_7d` | 169 | 2017.42 | 8189.70 |
| `same_hour_same_weekday_mean_4w` | 672 | 1881.84 | 8382.08 |
| `civil_utc_offset_hours` | 0 | 10 | 11 |

These are range checks only. No target-correlation analysis was done.

## Outputs (git-ignored; SHA-256)

The table has 70 columns per profile. Lineage tables have 457,776 rows per
profile. Annual partitions:

| Dataset | Profile | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| features | primary | `41a6c163…` | `35412625…` | `d242e991…` | `651185fb…` | `fd8d457b…` |
| features | zero lag | `dc28f032…` | `8e3b6823…` | `679e6643…` | `11b7bec3…` | `dde81f0c…` |
| audit | primary | `79f6a3a8…` | `888acbe7…` | `380dd6e9…` | `86b9c8fd…` | `cf7eebd3…` |
| audit | zero lag | `53fb9f59…` | `8e7faf5d…` | `86610c29…` | `a2d0209d…` | `66da5fa5…` |

Full hashes are in `results/runs/phase3_features_20261016_r2/run_manifest.json`
(SHA-256 `4cb6a3a4332f8de9ed2a43550e7996e5c4af0fab0a756aa54eca6769c77513eb`).
The manifest records:
- the input Phase 2 run and its manifest hash (`da3ed96c…`);
- the four config hashes;
- the holidays version;
- the code commit and dirty paths;
- the environment;
- all checks and output hashes.

The DuckDB report is `duckdb_feature_reconciliation.json` (`243f0b47…`).

## Tests added

`tests/test_features.py` (22 tests):
- hand-calculated lags and windows, both profiles;
- cutoff assertion;
- future perturbation and truncation equivalence (with missing hours), both
  profiles;
- the horizon-24 `lag_24h` case, both profiles;
- DST start and end (24 market horizons; civil hours 0,1,3…23,0 and 1,2,2,3…23;
  offsets 10/11);
- Victorian holidays, substitute days, adjacent days, the year boundary, and
  the market/civil date split on 2023-12-31 23:00;
- leap day;
- missing hour and insufficient lookback (rows retained);
- unique keys, stable sort and determinism under shuffled input;
- profile labelling.

Also: `tests/test_phase3_pipeline.py` (end-to-end CLI), a real-data bounded
integration test, `tests/test_docs.py`, and a git-state regression test.

## Claim status

| Claim | Basis |
|---|---|
| No used historical value has a source endpoint after its cutoff | Fresh run, two independent checks (pipeline audit + DuckDB) |
| Vectorised features equal the physically truncated reference for all rows | Fresh run (0 disagreements) |
| Future data cannot change features at an origin | Offline perturbation test |
| Values were published by the origin in real time | **Not established** (snapshot values; D-07 is an assumption) |
| Holiday calendar correctness | `holidays` 0.106 (third-party), spot-checked by tests against known VIC dates |

## Closeout (2026-10-16, after owner approval)

**Clean tree.** The owner's `CLAUDE.md` edit was reviewed and committed as
`144ed3a`:
- 133 added lines, 0 deletions (a token-efficient working protocol);
- no secrets or machine paths;
- no weakened controls (its §7 keeps every quality gate).

Closeout tooling was committed as `f35ff79`.

**Canonical run: `phase3_features_20261016_clean`.** Code `f35ff79`,
`dirty: false`. SLURM 33158036, COMPLETED 0:0, which also ran:
- the full gate: 192 offline and 5 integration tests passed; lint, format and
  strict mypy clean;
- `build-features`: leakage checks passed, 41,616 rows per profile;
- `validate-features`: 9 of 9 checks passed;
- `compare-feature-runs` against r2.

Manifest SHA-256 `e407910b719fea439d14578f4062ef89277ff80758895c342d6c3f2cedb6322b`.

**Comparison with `phase3_features_20261016_r2`** (superseded evidence, kept on
disk): **not byte-identical, logically identical.**

| Dataset | Rows | Bytes identical | Schema identical (without metadata) | Frames exactly equal | Logical hash (both runs) |
|---|---|---|---|---|---|
| `primary_operational/audit` | 457,776 | no | yes | yes | `b0191db7a8216ce8f3675a23869871ab8ecd50bcb719bf3c52b9f711a6b15137` |
| `primary_operational/features` | 41,616 | no | yes | yes | `d7be273dc56adc6f8425b94a2740b244ae2f61b47b79a260d0bac3002c8fd0ca` |
| `sensitivity_zero_lag/audit` | 457,776 | no | yes | yes | `2b51db0345866fca0ebce7c1666821cc0bf548e4549f60b68fbc879c9932b1b1` |
| `sensitivity_zero_lag/features` | 41,616 | no | yes | yes | `af6506a726b1c55772c072df5758ecf13bac5eea23f93e110c9d1c1de3c8d476` |

The only Parquet metadata key that differs is `nem_forecast.run_id`. Every
build writes it into the schema metadata, so byte identity is impossible by
design. Logical identity was checked on:
- the Arrow schema without metadata;
- row counts;
- exact `assert_frame_equal`;
- a SHA-256 over `pandas.util.hash_pandas_object` values plus column names
  and dtypes.

Report results:
- `eligibility_report.json` and `schema_report.json` are byte-identical;
- the Markdown reports are identical after normalising the run ID in their
  title line;
- `leakage_checks.json` is identical apart from timing.

Canonical output hashes (prefixes; full values in the manifest):

| Profile | Dataset | Annual partitions |
|---|---|---|
| primary_operational | audit | 2022: `b5d3db44adce3ba6…`, 2023: `1fca37e4ed816d03…`, 2024: `28d15f48e60ff3d1…`, 2025: `14cf045f62ef5bc8…`, 2026: `80728119920eb808…` |
| primary_operational | features | 2022: `2f41cc03bcd6a0ad…`, 2023: `e3a14e0570d5a24b…`, 2024: `a57299cf18346c58…`, 2025: `95e4fc45aa016b39…`, 2026: `8dcd8a65f12bc204…` |
| sensitivity_zero_lag | audit | 2022: `626d00a10162e89c…`, 2023: `8bd584111d9e4c4d…`, 2024: `66040cb9a684a6a9…`, 2025: `d54573ff9e21cac7…`, 2026: `6e7e496cc867de32…` |
| sensitivity_zero_lag | features | 2022: `3d4cb6674458ab27…`, 2023: `3181305d082222a0…`, 2024: `d456c27750c68902…`, 2025: `25997663aca321af…`, 2026: `b04327c961e99e4e…` |

**Tag:** `feature-contract-v1` (annotated, immutable) is on the closeout commit
that records this run.
