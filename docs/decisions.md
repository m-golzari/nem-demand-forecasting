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
| A-04 | `history.end_date: null` means “latest complete date”, resolved and frozen in the data snapshot manifest | “Latest complete date” moves over time and is not reproducible otherwise | — |
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

## Open

| ID | Question | Phase |
|---|---|---|
| O-01 | ~~Verify NEM market time~~ **Resolved 2026-10-14 (Phase 1)**: AEMO Spot Market Operations Timetable v1.10 says time references are AEST; raw DST-date check shows fixed UTC+10 | — |
| O-02 | ~~Source table, definition, interval semantics~~ **Resolved 2026-10-14 (Phase 1)**: `DISPATCHREGIONSUM`; `SETTLEMENTDATE` = interval end; `TOTALDEMAND` defined in D-01 (see `docs/aemo_schema_audit.md`) | — |
| O-03 | **Owner review required.** Which row represents demand in intervention intervals (`INTERVENTION` 0 = pricing run, 1 = physical run)? Not stated for DISPATCHREGIONSUM; the April 2024 sample has no intervention rows. All rows are preserved and flagged until decided. `RUNNO` is always 1; no PK duplicates were observed | before Phase 2 |
| O-04 | ~~Adapter~~ **Resolved 2026-10-14 (Phase 1)**: direct NEMWeb MMSDM monthly archives (daily archives only cover the last ~12 months); `nemosis` not used | — |
| O-05 | Historical publication latency is **not established** (`LASTCHANGED` is not first publication; 7/2016 VIC1 records changed up to 25 min after interval end). Recommendation: 1 h feature availability lag in the primary model, with a 0-lag sensitivity run | 3 (owner decides) |
| O-06 | Calendar features: market time vs Victorian civil time (DST) for hour-of-day; holidays use civil dates | 3 |
| O-07 | Primary point metric (MAE vs WAPE), folds, holdout, seeds in `configs/backtest.yaml` | 4 |
| O-08 | How point baselines produce p10/p90 (e.g. fold-fitted empirical residual quantiles) | 4 |
| O-09 | ~~Not a git repository~~ **Resolved 2026-10-14**: git initialised; owner authorised public repo `m-golzari/nem-demand-forecasting` | — |
| O-10 | ~~Project licence~~ **Resolved 2026-10-14**: MIT for code/docs; external data excluded and governed by source terms. Data redistribution remains forbidden unless separately approved | — |
