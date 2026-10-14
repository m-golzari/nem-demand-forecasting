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

## Open

| ID | Question | Phase |
|---|---|---|
| O-01 | Verify NEM market time = fixed UTC+10 from current AEMO documentation | 1 |
| O-02 | Source table (`DISPATCHREGIONSUM` vs alternatives), official `TOTALDEMAND` definition, `SETTLEMENTDATE` interval-end semantics | 1 |
| O-03 | Duplicate/revision/intervention rule (`INTERVENTION`, `RUNNO`, `LASTCHANGED`, …) | 1 |
| O-04 | Direct NEMWeb vs `nemosis` adapter | 1 |
| O-05 | Publication latency at the origin; whether a feature availability lag is needed | 1 (measure) / 3 (decide) |
| O-06 | Calendar features: market time vs Victorian civil time (DST) for hour-of-day; holidays use civil dates | 3 |
| O-07 | Primary point metric (MAE vs WAPE), folds, holdout, seeds in `configs/backtest.yaml` | 4 |
| O-08 | How point baselines produce p10/p90 (e.g. fold-fitted empirical residual quantiles) | 4 |
| O-09 | ~~Not a git repository~~ **Resolved 2026-10-14**: git initialised; owner authorised public repo `m-golzari/nem-demand-forecasting` | — |
| O-10 | ~~Project licence~~ **Resolved 2026-10-14**: MIT for code/docs; external data excluded and governed by source terms. Data redistribution remains forbidden unless separately approved | — |
