# Feature registry (feature set v1, Phase 3)

A feature may be used only if it is registered here. Adding a feature requires:
- a row here (business rationale, exact formula, lookback, source timestamp,
  availability rule);
- a decision-log entry;
- tests.

The code is in `src/nem_forecast/features/`; the config in `configs/features.yaml`.

## Notation and availability rule

- `O`: forecast origin, `D 00:00` NEM market time (UTC+10). `h = 1..24`.
- `T = O + (h−1) h`: target hour label, canonical `[T, T+1h)`.
- Availability profiles (owner decision D-07):
  - `primary_operational`: `C = O − 1 h`. This is a modelling assumption, not
    a measured publication delay.
  - `sensitivity_zero_lag`: `C = O`. **Sensitivity only.**
- **Hourly observation `H`.** The hourly mean of AEMO dispatch `TOTALDEMAND`
  estimates for the intervals ending `H+00:05 … H+01:00` (Phase 2, run
  `phase2_vic1_hourly_20261014_full`). Its **actual latest source endpoint**
  is Phase 2's `last_source_settlementdate`, which equals `H + 1 h` for every
  complete hour (checked in each run).
- **Usable observation.** An observation is usable at a row only if it is
  complete **and** its actual latest source endpoint is `≤ C`. The cutoff is
  applied to the source endpoint, never to the hourly label. Under
  `primary_operational` the latest usable label is therefore `O − 2 h`.
- **Unusable values** are null. The row records `<feature>__available = false`
  and `<feature>__missing_reason`, one of:
  - `after_cutoff`: structural, the endpoint is later than `C`;
  - `insufficient_history`: the required label precedes 2022-01-01 00:00;
  - `missing_source_hour`: the hour is absent or incomplete in Phase 2;
  - `source_endpoint_after_cutoff`: defensive.

  Nothing is substituted, imputed, or computed from a partial window.
- **Lineage** (audit table, one row per design row × feature):
  - represented start and end label;
  - the number of required and used observations;
  - the latest observation label used;
  - the latest source endpoint used;
  - the cutoff, and `endpoint_le_cutoff`.

## Historical demand features

| Name | Business rationale | Exact formula | Lookback / required labels | Latest source endpoint | Availability |
|---|---|---|---|---|---|
| `lag_24h` | Same hour yesterday; the strongest daily-cycle baseline | `y(T − 24 h)` | 1 label: `T − 24 h` | `T − 23 h` | Usable iff `T − 23 h ≤ C`. Under the primary profile, **unavailable at h = 24** (`T − 23h = O > C`): null, `after_cutoff`. Available at h = 24 only under zero lag |
| `lag_48h` | Same hour two days ago; robust when yesterday is atypical | `y(T − 48 h)` | `T − 48 h` | `T − 47 h` | Always `≤ C` for h ≤ 24 |
| `lag_168h` | Same hour last week; weekly cycle baseline | `y(T − 168 h)` | `T − 168 h` | `T − 167 h` | Always `≤ C` |
| `recent_24h_mean` | Current demand level | mean of `y(H)`, `H ∈ [C − 24 h, C − 1 h]` | 24 consecutive labels ending `C − 1 h` (origin-relative; same for all horizons) | `C` | All 24 must be usable |
| `recent_24h_std` | Recent volatility | sample std (ddof = 1) of the same 24 values | as above | `C` | as above |
| `recent_24h_min` / `recent_24h_max` | Recent range (overnight trough, daily peak) | min / max of the same 24 values | as above | `C` | as above |
| `recent_168h_mean` | Weekly level, smoothing day-of-week effects | mean of `y(H)`, `H ∈ [C − 168 h, C − 1 h]` | 168 labels ending `C − 1 h` | `C` | All 168 must be usable |
| `recent_168h_std` | Weekly variability | sample std (ddof = 1) of the same 168 values | as above | `C` | as above |
| `same_hour_mean_7d` | Typical level at the target's hour of day | mean of `y(T − 24k h)` for the 7 smallest `k ≥ 1` with `T − 24k h + 1 h ≤ C` | 7 labels. Primary: `k = 1..7` for h ≤ 23 and `k = 2..8` at h = 24. Zero lag: `k = 1..7` | `T − 24k₀ h + 1 h ≤ C` | The window shifts back by availability, as defined. It never uses data after `C`. All 7 must be complete |
| `same_hour_same_weekday_mean_4w` | Typical level at the same hour and weekday | mean of `y(T − 168k h)`, `k = 1..4` | 4 labels; 672 h lookback | `T − 167 h` | All 4 must be usable |

## Target calendar features (deterministic; known at any origin)

Market-time fields use fixed UTC+10. Civil-time fields use
`Australia/Melbourne` and are converted from the canonical UTC target
timestamp. The two are never mixed.

| Name | Rationale | Formula |
|---|---|---|
| `horizon` | Error grows with lead time; the target-hour position | `1..24` |
| `market_hour`, `market_weekday` (Mon = 0), `market_month`, `market_day_of_year`, `market_is_weekend` | Market-time calendar, consistent with the target's time base | From `T` in UTC+10 |
| `market_hour_sin/cos`, `market_weekday_sin/cos`, `market_doy_sin/cos` | Periodic encodings remove the artificial jumps 23→0, Sun→Mon, 31 Dec→1 Jan for distance-based or linear models. Raw integers are kept for trees | `sin/cos(2π·hour/24)`, `sin/cos(2π·weekday/7)`, `sin/cos(2π·(doy−1)/365.25)` (mean year, so leap years stay on one cycle) |
| `civil_timestamp`, `civil_date`, `civil_hour`, `civil_weekday`, `civil_is_weekend` | Human activity follows clock time, which shifts by 1 h under daylight saving | `T` converted to `Australia/Melbourne` |
| `civil_utc_offset_hours`, `civil_is_dst` | Makes the daylight-saving regime explicit | `10` or `11`; `dst() ≠ 0` |
| `civil_is_public_holiday`, `civil_public_holiday_name` | Holidays reduce commercial demand | `civil_date` in the `holidays` 0.106 AU/VIC calendar (substitute days included, e.g. 2022-12-27, 2023-01-02) |
| `civil_is_day_before_public_holiday`, `civil_is_day_after_public_holiday` | Bridge and shoulder-day effects | `civil_date ± 1 day` in the holiday calendar |

Calendar availability: target timestamps are deterministic. Holidays come from
a published calendar. For a next-24-hour forecast, a holiday flag is needed at
most about one day ahead.

**[INF] Limitation:** one-off holidays declared at short notice could, in
principle, be unknown at an origin. An example is the 2022-09-22 National Day
of Mourning, whose announcement date was not verified here. All other holidays
in the calendar are fixed in law well ahead. Melbourne Cup Day is a
metropolitan holiday; some regional councils substitute other days.

## Other columns (not features)

| Column | Meaning |
|---|---|
| `target_dispatch_totaldemand_hourly_mean_mw`, `target_available` | The label: the hourly mean of AEMO dispatch `TOTALDEMAND` estimates for VIC1, MW (Phase 2). It is never an input |
| `availability_profile`, `availability_lag_hours`, `cutoff_utc` | Profile identity and cutoff `C` |
| `eligible_lag24_baseline` | `target_available ∧ lag_24h__available` |
| `eligible_lag168_baseline` | `target_available ∧ lag_168h__available` |
| `eligible_initial_ml_features` | `target_available` ∧ every historical feature is available, or unavailable only for the structural `after_cutoff` reason (left null, not substituted) |

## Not registered (forbidden or out of scope for v1)

- Observed target-hour weather (`realised_weather_oracle` only, Phase 5).
- Any centred window.
- Any statistic fitted across folds.
- Target-correlation-selected features.
