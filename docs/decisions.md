# Decision log

Format: ID · date · status · decision · rationale · revisit trigger.
Status values: **Frozen** (owner-fixed scope), **Adopted** (implementer default,
owner may override), **Open** (needs a decision in the named phase).

## Frozen by owner (CLAUDE.md §3)

| ID | Decision |
|---|---|
| F-01 | Region `VIC1`; target AEMO `TOTALDEMAND`; unit MW |
| F-02 | Hourly target = time-weighted mean MW, never a sum |
| F-03 | Origin daily 00:00 NEM market time; horizon next 24 hourly intervals |
| F-04 | Outputs p10/p50/p90; models: seasonal-naive baselines, then LightGBM |
| F-05 | Primary model uses demand + calendar features only |
| F-06 | History from 2022-01-01; Python 3.11; package `nem_forecast`; Parquet + DuckDB |

## Adopted in Phase 0 (2026-10-14)

| ID | Decision | Rationale | Revisit if |
|---|---|---|---|
| A-01 | Hourly bins are left-closed `[start, start+1h)` and labelled by start. **Owner-approved 2026-10-14**: horizon 1 is `[00:00, 01:00)` market time, labelled 00:00; interval-ending source records keep their source timestamps and are mapped by represented interval into start-labelled windows. Change only if official source semantics contradict it. | Matches “origin + next 24 intervals”: h=1 begins at the origin; standard convention for pandas/DuckDB resampling | Owner prefers hour-ending labels (AEMO style) |
| A-02 | Market timezone represented as a fixed offset (UTC+10), never `Australia/Melbourne` | CLAUDE.md §8; avoids DST errors | Phase 1 official documentation disagrees |
| A-03 | Frozen contract fields are `Literal` types in code, not just YAML values | A YAML edit cannot silently change scope | Owner changes scope |
| A-04 | ~~`history.end_date: null`~~ **Superseded 2026-10-14**: history end frozen by the owner at 2026-09-30 (snapshot end month 2026-09) | Reproducibility | — |
| A-05 | An hour is complete only with 12/12 intervals (`min_hourly_interval_coverage: 1.0`) | Conservative default; configurable | Phase 2 audit shows systematic gaps |
| A-06 | Project root resolved from an explicit argument, then `NEM_FORECAST_ROOT`, then package location; never the CWD | CLAUDE.md §17 | — |
| A-07 | Phase 0 dependencies limited to pydantic, PyYAML, Typer (+ dev tools); `pydantic-settings` not used yet | Small dependency set (CLAUDE.md §18) | Settings needs grow |
| A-08 | “Day-ahead” is interpreted as “next 24 hours issued at market midnight” | The fixed origin and horizon define the product; avoids confusion with AEMO pre-dispatch | Owner wants an earlier (D-1) origin |

## Phase 1 decisions (2026-10-14)

| ID | Decision | Rationale | Revisit if |
|---|---|---|---|
| D-01 | Target stays `DISPATCHREGIONSUM.TOTALDEMAND` (MW), described as the **hourly mean of dispatch total demand**. Official definition: NEMDE's 50% POE forecast of scheduled demand at the regional reference node for the **end** of each 5-minute interval. Excludes non-scheduled generation and rooftop PV. Not metered average demand, and not AEMO “operational demand” | Owner-fixed target; the unit is confirmed as MW; the meaning is now documented rather than inferred from the name. **Discrepancy with the CLAUDE.md wording is recorded for owner awareness** | Owner prefers a measured target (e.g. operational demand) |
| D-02 | The sample window is end-exclusive market dates [2024-04-04, 2024-04-11) = 2016 intervals. The download unit is the official monthly archive (April 2024, 4.57 MB, all regions), the smallest official unit covering 2024 | Daily NEMWeb archives no longer cover 2024; raw files must be kept unchanged | — |
| D-03 | Raw archives are written once (`.partial` then rename), hashed, manifested, made read-only, and never overwritten. A changed upstream file is a new artefact for comparison | CLAUDE.md §7; upstream archives are re-issued (2024-08 to 2024-12 on 2026-05-19) | — |
| D-04 | Canonical timestamps: `interval_end_market = SETTLEMENTDATE` + fixed UTC+10; start = end − 5 min; UTC by conversion; `hour_start` = floor(start, 1 h). Source strings are retained | Official interval-ending semantics + owner decision A-01 | Official semantics change |
| D-05 | Downloads longer than 7 days are refused unless `--owner-approved-large-download` is passed | Enforces the multi-year owner gate in code | Owner approves Phase 2 history |

## Phase 1B owner decisions (2026-10-14)

| ID | Decision | Rationale / evidence | Revisit if |
|---|---|---|---|
| A-01 (wording) | Keep start labels. Document **explicit endpoint normalisation**: AEMO's source support `(00:00, 01:00]`, carried by interval-ending records 00:05…01:00, is represented canonically as `[00:00, 01:00)` labelled 00:00. All original timestamps are preserved. Boundary mapping is tested | Owner instruction | Official semantics contradict it |
| D-01 (wording) | The target is described only as the “hourly mean of AEMO dispatch `TOTALDEMAND` estimates for VIC1”. It is never called measured consumption, metered load, total underlying demand, or observed average demand | Owner instruction; AEMO definition | — |
| D-06 | Intervention selection per (region, interval). One ordinary record → retain. Exactly one physical (`INTERVENTION=1`) + one pricing (`INTERVENTION=0`) → select physical and record the difference. Any other combination → quarantine and stop for owner review. Never both rows, never an average. Both rows are preserved upstream | Owner decision. Flag values confirmed from AEMO *Intervention Pricing Methodology* v2.3 (effective 3 June 2024) §3.3: base case target run = `Intervention=1`, pricing run = `Intervention=0` | Documentation changes or unexpected patterns |
| D-07 | Primary operational features may use demand intervals ending ≤ origin − 1 h. This is a **modelling assumption**, not a measured publication delay. A zero-lag profile is kept as a labelled sensitivity (`configs/feature_availability.yaml`). `LASTCHANGED` is never treated as proof of availability | Owner decision; Phase 1 found latency not established | Archived availability evidence appears |
| D-08 | Historical snapshot: 57 official monthly archives, 2022-01 to 2026-09, retrieved 2026-10-14 into git-ignored `data/raw`. It is a snapshot of historical records, not a reconstruction of originally published files. Download stops on filename ambiguity, a missing month, an invalid archive, a hash inconsistency or an unsupported schema | Owner authorisation | New snapshot requested |

## Phase 2 decisions (2026-10-14)

| ID | Decision | Rationale | Revisit if |
|---|---|---|---|
| D-09 | A public, path-neutral snapshot lock (`configs/data_snapshots/<snapshot>.lock.json`) pins every input archive. It records month, URL, filename, bytes, SHA-256, retrieval time, report version and archive member. Phase 2 refuses inputs that do not match it | Owner instruction; reproducibility without committing data | New snapshot |
| D-10 | Hourly completeness = all 12 expected endpoints exactly once after D-06 selection + finite values + VIC1 + consistent `DISPATCHINTERVAL` + no quarantined record. Incomplete hours are kept with reason flags and a null target, and excluded from model-ready data | Owner specification; CLAUDE.md §9 | — |
| D-11 | Outputs use annual partitions: `intervals` and `canonical_hourly` in `data/interim/aemo_vic1/<run_id>/`, `model_ready` in `data/processed/vic1_hourly_target/<run_id>/`, reports and manifest in `results/runs/<run_id>/`. All are git-ignored; run IDs are never reused | Avoids tiny files; CLAUDE.md §14, §17 | — |
| D-12 | Phase 2 is validated by an independent path: a stdlib `csv` re-read of the raw archives plus DuckDB re-derivation of selection, hour mapping and means, compared with the outputs (tolerance 10⁻⁹ MW) | Guards against shared-code errors | — |
| D-13 | `configs/forecast.yaml` history end is frozen at 2026-09-30, matching the snapshot | Owner freeze (Phase 1B) | — |

## Phase 3 decisions (2026-10-16)

| ID | Decision | Rationale | Revisit if |
|---|---|---|---|
| D-14 | Design table: every market date 2022-01-01 … 2026-09-30 × horizons 1–24 = 41,616 rows per profile. Rows are never dropped; eligibility is expressed by flags | Owner instruction; warm-up is derived, not hard-coded | — |
| D-15 | Availability is decided per required observation from its **actual** latest source endpoint (Phase 2 `last_source_settlementdate`) `≤ origin − lag`. The latest usable label is `origin − 2 h` (primary) or `origin − 1 h` (zero lag) | Owner instruction: the cutoff applies to source endpoints, not labels | — |
| D-16 | `lag_24h` at h = 24 under `primary_operational` is null, `__available = false`, reason `after_cutoff`. No substitution, and the cutoff is not weakened. `eligible_initial_ml_features` tolerates only this structural null | Owner instruction | Owner changes the availability rule |
| D-17 | Recent windows are origin-relative (the 24/168 labels ending `cutoff − 1 h`). Same-hour 7-day mean = the 7 most recent same-hour days whose endpoint is `≤ cutoff`. Same-weekday 4-week mean = `T − 168k h`, `k = 1..4`. Std uses ddof = 1. A window is null unless every required hour is usable | Explicit, auditable definitions | — |
| D-18 | Civil calendar from canonical UTC via `Australia/Melbourne`. Holidays from `holidays` **0.106** (pinned; AU/VIC, substitutes included). Market-time and civil-time fields are separately named | Owner instruction; holiday data affect features | Package update needs a new feature set version |
| D-19 | Each build re-derives every historical value with an independent per-origin builder on physically truncated input, plus a DuckDB recomputation of lags and `recent_24h_mean`. The build fails on any disagreement or any endpoint after the cutoff | Leakage proof obligation | — |
| D-20 | Outputs: `data/processed/features/<run>/profile=<p>/year=Y/`, `data/interim/feature_audit/<run>/profile=<p>/year=Y/`, reports in `results/runs/<run>/`. The profile is in the path, a column and the Parquet metadata. Run IDs are never reused | Profiles can never overwrite or be confused | — |

## Open

| ID | Question | Phase |
|---|---|---|
| O-01 | ~~Verify NEM market time~~ **Resolved 2026-10-14 (Phase 1)**: AEMO Spot Market Operations Timetable v1.10 says time references are AEST; raw DST-date check shows fixed UTC+10 | — |
| O-02 | ~~Source table, definition, interval semantics~~ **Resolved 2026-10-14 (Phase 1)**: `DISPATCHREGIONSUM`; `SETTLEMENTDATE` = interval end; `TOTALDEMAND` defined in D-01 (see `docs/aemo_schema_audit.md`) | — |
| O-03 | ~~Intervention rows~~ **Resolved 2026-10-14 by D-06** (physical run selected; others quarantined) | — |
| O-04 | ~~Adapter~~ **Resolved 2026-10-14 (Phase 1)**: direct NEMWeb MMSDM monthly archives (daily archives only cover the last ~12 months); `nemosis` not used | — |
| O-05 | ~~Availability lag~~ **Resolved 2026-10-14 by D-07** (1 h buffer as a modelling assumption; zero-lag sensitivity) | — |
| O-06 | Calendar features: market time vs Victorian civil time (DST) for hour-of-day; holidays use civil dates | 3 |
| O-07 | Primary point metric (MAE vs WAPE), folds, holdout, seeds in `configs/backtest.yaml` | 4 |
| O-08 | How point baselines produce p10/p90 (e.g. fold-fitted empirical residual quantiles) | 4 |
| O-09 | ~~Not a git repository~~ **Resolved 2026-10-14**: git initialised; owner authorised public repo `m-golzari/nem-demand-forecasting` | — |
| O-10 | ~~Project licence~~ **Resolved 2026-10-14**: MIT for code/docs; external data excluded and governed by source terms. Data redistribution remains forbidden unless separately approved | — |
