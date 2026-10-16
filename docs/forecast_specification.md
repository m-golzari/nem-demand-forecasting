# Forecast specification (contract version 1)

Status: **frozen** for target, unit, horizon and origin; owner decisions through
Phase 1B are applied (2026-10-14). Items marked *OPEN* are deferred to the
named phase (see `docs/decisions.md`).

Machine-enforced copies:
- `configs/forecast.yaml`, validated by `nem_forecast.config.ForecastConfig`;
- `configs/feature_availability.yaml`;
- executable origin, horizon and time rules in `nem_forecast.contract`.

## 1. Target

> **What the target is.** The hourly mean of AEMO dispatch `TOTALDEMAND`
> estimates for VIC1, in MW.
>
> **What it is not.** Measured consumption, metered load, total underlying
> demand, or observed average demand.

| Item | Value |
|---|---|
| Region | `VIC1` (filter and assert `REGIONID == "VIC1"`) |
| Source | AEMO `DISPATCHREGIONSUM.TOTALDEMAND`, NEMWeb MMSDM monthly archives |
| Source definition | AEMO *Demand Terms in EMMS Data Model* v17.0 §2.1.2. It is “the forecast demand at the Regional Reference Node (RRN) that is met by local generation from scheduled and semi-scheduled generating units and scheduled BDUs plus interconnector imports, excluding the local demand of scheduled loads and scheduled BDUs and the allocated interconnector losses, but including the demand met by Wholesale Demand Response”. NEMDE calculates it before dispatch optimisation, from start-of-interval SCADA values plus `DemandForecast` and `AggregateDispatchError`. Its forecast type is 50% POE, and the value is an estimate **for the end of each 5-minute interval** |
| Exclusions | It excludes non-scheduled generation and behind-the-meter generation such as rooftop PV, so it is **not** total underlying demand. It excludes the local demand of scheduled loads and scheduled BDUs, and allocated interconnector losses. It is not AEMO “operational demand” (a separate, measured product) |
| Intervention intervals | The physical (base case target) run record is used. See §6 and D-06 |
| Unit | MW (power) |
| Native frequency | 5-minute dispatch intervals (verified) |
| Model target | The hourly **mean MW** of the 12 selected end-of-interval `TOTALDEMAND` estimates whose intervals lie in the hour. This is the time-weighted mean for 12 equal intervals. MW are never summed. Energy, if reported, is MWh = mean MW × 1 h and labelled MWh |

## 2. Time and endpoint normalisation

| Item | Value |
|---|---|
| Market timezone | NEM market time = AEST, fixed UTC+10, no daylight saving (verified) |
| Source timestamp | `SETTLEMENTDATE` is the **end** of a 5-minute interval `(end − 5 min, end]`. It is naive market time and preserved verbatim (`settlementdate_source`) |
| Canonical storage | Timezone-aware UTC instants plus market-time columns. Original source strings are retained |
| Canonical hour | Half-open `[start, start + 1 h)`, labelled by **start** (owner decision A-01) |

**Explicit endpoint normalisation.** AEMO's source support for the first hour
is `(00:00, 01:00]`, carried by the 12 interval-ending records stamped
00:05, 00:10, …, 01:00. The canonical representation of the same hour is the
half-open window `[00:00, 01:00)`, labelled **00:00**. The record stamped 00:00
belongs to the previous hour (`[23:00, 24:00)` of the prior market date).

This is a relabelling of interval endpoints for the canonical store, not a time
shift. The source stamps are preserved. The boundary mapping is tested in
`tests/test_availability.py::test_endpoint_normalisation_boundaries` and
`tests/test_time_semantics.py`.

Victorian civil time (AEST/AEDT) differs from market time by one hour during
daylight saving. Market dates never have 23 or 25 hours.

## 3. Origin and horizon

- Origin: every market date `D` at **00:00 NEM market time** (= `D-1 14:00 UTC`).
- Horizon `h = 1..24`: target hour `[D 00:00 + (h−1) h, D 00:00 + h h)` market
  time. Horizon 1 is `[00:00, 01:00)`, labelled 00:00.
- Every origin has exactly 24 ordered target hours.
- Terminology: this is a *next-24-hour forecast issued at market midnight*, not
  an AEMO pre-dispatch or market day-ahead product.

## 4. Outputs

`p10`, `p50`, `p90` of hourly mean MW per target hour. p10–p90 is a nominal 80%
interval; calibration is measured, never assumed. Quantile crossing is reported
before any correction.

## 5. Feature availability (owner decision D-07)

- **Primary operational feature set:** a forecast at origin `T` may use demand
  intervals **ending no later than `T − 1 hour`**. This 1-hour buffer is a
  conservative **modelling assumption**, not a measured AEMO publication delay.
  Historical availability at the origin was not established. `LASTCHANGED` is
  not treated as proof of public availability.
- **Zero-lag sensitivity:** data ending at or before `T`. It is kept in
  `configs/feature_availability.yaml` as `sensitivity_zero_lag`, labelled
  “SENSITIVITY ONLY”, and used for later development analysis. It is never
  reported as an operational result.
- Other primary features: target calendar values and known holidays.
  Weather is **excluded** from the primary model unless archived forecast
  vintages and their availability at the origin are proven. Observed
  target-hour weather is allowed only in a separately labelled realised-weather
  oracle experiment.
- Every feature is registered in `docs/feature_registry.md`.

## 6. Data quality, interventions, late/revised data

- **Completeness:** an hour is complete when interval coverage ≥
  `min_hourly_interval_coverage` (default 1.0 = 12/12). Incomplete hours are
  flagged upstream and excluded from model-ready data by default. Nothing is
  silently dropped, clipped, or interpolated.
- **Selection per (region, interval)** (owner decision D-06). AEMO's
  *Intervention Pricing Methodology* v2.3 §3.3 states that the base case target
  (physical/outturn) run is flagged `INTERVENTION=1` and the pricing (what-if)
  run `INTERVENTION=0`.
  - One ordinary record (`INTERVENTION=0`): retain it.
  - Exactly one physical and one pricing record: select the **physical**
    record for the primary target and record `physical − pricing`
    `TOTALDEMAND`. Both rows are preserved upstream, never both in an hourly
    mean, and never averaged.
  - Any other combination (a lone physical row, two pricing rows, more than
    two rows, an undocumented flag, or `RUNNO ≠ 1`): **quarantine** the
    interval and stop for owner review.
- **Revisions:** upstream archives are a retrieval-time snapshot (§7). A
  changed upstream file is a new artefact, detected by hash.

## 6a. Phase 2 implementation (`nem_forecast.target`)

- **Target column:** `dispatch_totaldemand_hourly_mean_mw`. It is the hourly
  mean of AEMO dispatch `TOTALDEMAND` estimates for VIC1, in MW, i.e. the
  arithmetic mean of the 12 selected end-of-interval estimates for intervals
  ending `H+00:05 … H+01:00`. Equal 5-minute durations make this the
  duration-weighted mean.
- **Complete hour:** all 12 expected endpoints are present exactly once after
  primary-record selection, values are finite, the region is VIC1,
  `DISPATCHINTERVAL` is consistent, and no record in the hour is quarantined.
  Row count alone is insufficient.
- **Incomplete or invalid hours** are kept in `canonical_hourly` with
  `quality_reasons` and a null target. They are excluded from `model_ready`.
  Nothing is interpolated, clipped, averaged over fewer records, or substituted
  with a pricing-run record.
- **No feature availability buffer** is applied when building targets. D-07
  applies in Phase 3.
- **Input pinning:** inputs come from the locked snapshot
  (`configs/data_snapshots/<snapshot>.lock.json`). The run manifest records
  the lock hash, input hashes, git commit and dirty flag, resolved
  configuration, row counts, coverage, quality results and output hashes.
- **Result (run `phase2_vic1_hourly_20261014_full`):** 41,616 of 41,616 hours
  complete, from 2022-01-01 00:00 to 2026-09-30 23:00. Independently
  reconciled.

## 6b. Phase 3 feature dataset (`nem_forecast.features`)

- **Rows:** one per (profile, origin, horizon): 1,734 origins × 24 = 41,616
  per profile. All rows are kept, with eligibility flags.
- **Cutoff:** applied to each observation's actual latest source endpoint
  (`≤ origin − lag`). The latest usable hourly label is `origin − 2 h`
  (primary) or `origin − 1 h` (zero lag, sensitivity only).
- **Registry:** features, formulas, lookbacks and availability rules are in
  `docs/feature_registry.md`. `lag_24h` at h = 24 is structurally
  unavailable under the primary profile (null, flagged, not substituted).
- **Leakage proof:** see `docs/phase_reports/phase_3.md`.

## 7. History, cadence, folds, holdout

- History: market dates 2022-01-01 to 2026-09-30, i.e. interval ends from
  2022-01-01 00:05 to 2026-10-01 00:00. The end month 2026-09 is **frozen** in
  `configs/aemo_source.yaml` and the snapshot manifest. The data is a
  2026-10-14 snapshot of historical records, not a reconstruction of files as
  originally published.
- Update cadence: one forecast per market date at the origin.
- Fallback: a seasonal-naive forecast, identified in every response (Phase 9).
- **Backtest protocol (frozen 2026-10-16, `configs/backtest.yaml`, D-21…D-29):**
  - primary profile `primary_operational`;
  - primary metric MAE (MW); secondary metric aggregate WAPE;
  - 12 monthly development folds of origins, 2025-07-01 … 2026-06-30,
    grouped by origin;
  - **final holdout of origins 2026-07-01 … 2026-09-30, evaluated only with
    explicit owner approval**;
  - expanding training using matured outcomes only (origin ≤ fold start − 2
    days);
  - 180-day residual calibration window; hybrid weight fitted before it;
  - bootstrap seed 20261016 with 2,000 daily-block replicates.

  Random splits are forbidden.
