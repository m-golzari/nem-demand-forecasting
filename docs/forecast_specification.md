# Forecast specification (contract version 1)

Status: **frozen for target/unit/horizon/origin**. Items marked *VERIFY* are
assumptions that Phase 1 must confirm from official AEMO documentation and raw
data. Items marked *OPEN* are decisions deferred to the named phase (see
`docs/decisions.md`). Machine-enforced copy: `configs/forecast.yaml`, validated
by `nem_forecast.config.ForecastConfig`; executable origin/horizon rules:
`nem_forecast.contract`.

## 1. Target

| Item | Value |
|---|---|
| Region | `VIC1` (filter and assert `REGIONID == "VIC1"`) |
| Source field | AEMO regional `TOTALDEMAND` |
| Source table | `DISPATCHREGIONSUM` from NEMWeb MMSDM monthly archives (Phase 1, D-02) |
| Field meaning | Verified (D-01): NEMDE's 50% POE forecast of scheduled demand at the regional reference node for the **end** of each dispatch interval. Not a metered average |
| Unit | MW (power) |
| Native frequency | 5-minute dispatch intervals (verified: 288 per market date, uniform spacing) |
| Model target | Hourly **mean MW** of the 12 end-of-interval `TOTALDEMAND` values whose intervals lie in the hour (time-weighted mean = arithmetic mean for 12 equal intervals). MW are never summed. Energy, if reported, is MWh = mean MW × 1 h and labelled MWh. |

## 2. Time

| Item | Value |
|---|---|
| Market timezone | NEM market time = AEST, fixed UTC+10, no daylight saving (verified: AEMO timetable + raw DST-date check) |
| Canonical storage | timezone-aware UTC instants; source timestamp and market-time fields retained |
| Hourly bin | left-closed `[start, start + 1 h)`, labelled by **start**, defined in market time and stored in UTC |
| Source-to-bin mapping | Verified interval-ending: the bin `[00:00, 01:00)` contains the 12 records with `SETTLEMENTDATE` 00:05 … 01:00; source strings preserved (D-04) |

Victorian civil time (AEST/AEDT) differs from market time by one hour during
daylight saving. Market dates never have 23 or 25 hours.

## 3. Origin and horizon

- Origin: every market date `D` at **00:00 NEM market time** (= `D-1 14:00 UTC`).
- Horizon `h = 1..24`: target hour `[D 00:00 + (h-1) h, D 00:00 + h h)` market time.
- Every origin has exactly 24 ordered target hours (no DST in market time).
- Terminology: this is a *next-24-hour forecast issued at market midnight*. It
  is called “day-ahead” loosely in CLAUDE.md §1; it is not an AEMO pre-dispatch
  or market day-ahead product, and h = 1 starts at the origin.

## 4. Outputs

`p10`, `p50`, `p90` of hourly mean MW per target hour. p10–p90 is a nominal 80%
interval; calibration is measured, never assumed. Quantile crossing is reported
before any correction.

## 5. Feature availability

- Primary features: historical demand whose source interval **ends at or before
  the origin**, historical rolling summaries ending at/before the origin, target
  calendar values, known holidays.
- Primary feature set name: `demand_calendar`. Weather is **excluded** from the
  primary model (`weather_in_primary_model: false`) unless archived forecast
  vintages and their availability at the origin are proven.
- Observed target-hour weather is allowed only in a separately labelled
  realised-weather oracle experiment.
- Publication latency: **not established** historically (Phase 1). Recommended:
  a 1 h feature availability lag (latest usable record ends at origin − 1 h),
  with a 0-lag sensitivity run. Owner decides in Phase 3 (O-05).
- Every feature is registered in `docs/feature_registry.md`.

## 6. Data quality and late/revised data

- An hour is complete when interval coverage ≥ `min_hourly_interval_coverage`
  (default 1.0 = 12/12). Incomplete hours are flagged upstream and excluded
  from model-ready data by default; nothing is silently dropped, clipped, or
  interpolated.
- Duplicate/intervention resolution: **owner review required** (O-03). Until
  decided, all records are preserved and flagged; none are dropped.

## 7. History, cadence, folds, holdout

- History: 2022-01-01 to the latest complete market date. The end date is
  resolved and **frozen in the data snapshot manifest** at ingestion time.
- Update cadence: one forecast per market date at the origin.
- Fallback: a seasonal-naive forecast, identified in every response (Phase 9).
- Folds, step, minimum training window, final holdout, primary point metric
  (MAE or WAPE), and seeds: *OPEN (Phase 4)*. They will be frozen in
  `configs/backtest.yaml` before any holdout access. Random splits are forbidden.
