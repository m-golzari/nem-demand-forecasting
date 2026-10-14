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
| Source table | *OPEN (Phase 1)*: candidate `DISPATCHREGIONSUM`; intervention/run fields must be audited |
| Field meaning | *VERIFY (Phase 1)*: official definition, not inferred from the name |
| Unit | MW (power) |
| Native frequency | 5-minute dispatch intervals (*VERIFY*) |
| Model target | Hourly **time-weighted mean MW**; for 12 complete equal 5-minute intervals this is the arithmetic mean. MW are never summed. Energy, if reported, is MWh = mean MW × 1 h and labelled MWh. |

## 2. Time

| Item | Value |
|---|---|
| Market timezone | NEM market time, fixed UTC+10, no daylight saving (*VERIFY*) |
| Canonical storage | timezone-aware UTC instants; source timestamp and market-time fields retained |
| Hourly bin | left-closed `[start, start + 1 h)`, labelled by **start**, defined in market time and stored in UTC |
| Source-to-bin mapping | *VERIFY (Phase 1/2)*: if `SETTLEMENTDATE` is interval-ending, the bin `[00:00, 01:00)` contains intervals ending 00:05…01:00 |

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
- Publication latency: *OPEN (Phase 1 measures, Phase 3 decides)*. The contract
  currently permits data ending exactly at the origin; if measured latency makes
  that unrealistic, a lag will be added and recorded as a decision.
- Every feature is registered in `docs/feature_registry.md`.

## 6. Data quality and late/revised data

- An hour is complete when interval coverage ≥ `min_hourly_interval_coverage`
  (default 1.0 = 12/12). Incomplete hours are flagged upstream and excluded
  from model-ready data by default; nothing is silently dropped, clipped, or
  interpolated.
- Duplicate/revision resolution: *OPEN (Phase 1)*, only after the publication
  and version fields are documented.

## 7. History, cadence, folds, holdout

- History: 2022-01-01 to the latest complete market date. The end date is
  resolved and **frozen in the data snapshot manifest** at ingestion time.
- Update cadence: one forecast per market date at the origin.
- Fallback: a seasonal-naive forecast, identified in every response (Phase 9).
- Folds, step, minimum training window, final holdout, primary point metric
  (MAE or WAPE), and seeds: *OPEN (Phase 4)*. They will be frozen in
  `configs/backtest.yaml` before any holdout access. Random splits are forbidden.
