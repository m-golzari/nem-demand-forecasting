<div align="center">

# ⚡ NEM Demand Forecasting

### A reproducible pipeline for understanding and forecasting electricity demand in Victoria

**From public five-minute market records to trustworthy hourly forecasts.**

`Python` · `AEMO NEMWeb` · `Parquet` · `DuckDB` · `Time-series forecasting`

</div>

---

## Why this project exists

Electricity demand never stands still. It rises when people wake up, changes
with the weather, falls on public holidays, and can move sharply during unusual
market events. Energy companies and grid operators need to anticipate those
changes to plan generation, manage risk, and keep supply and demand in balance.

This project asks a simple question:

> **Using only information that would have been available at the time, how well
> can we forecast Victoria's electricity demand for the next 24 hours?**

Answering it responsibly takes more than fitting a machine-learning model. The
source data arrive in five-minute intervals, follow market-specific time rules,
contain special records during market interventions, and may be revised after
publication. A forecast can also appear accurate while accidentally using
information from the future.

The project therefore starts with the less glamorous, but essential, work:

- retrieving and preserving public market data;
- documenting what each field really means;
- handling market time and daylight saving correctly;
- resolving intervention records without double-counting them;
- building and validating an hourly target;
- recording enough provenance to reproduce every result; and
- designing later forecasting experiments to prevent look-ahead leakage.

The aim is not merely to produce a forecast. It is to build a small, auditable
data product whose results can be explained and trusted.

---

## The electricity market in plain English

### What is the NEM?

The **National Electricity Market (NEM)** is the interconnected wholesale
electricity market covering eastern and southern Australia. It links five
regions:

- Queensland (`QLD1`)
- New South Wales (`NSW1`)
- Victoria (`VIC1`)
- South Australia (`SA1`)
- Tasmania (`TAS1`)

Electricity is generated and consumed continuously, so the market is operated
in short dispatch intervals. The **Australian Energy Market Operator (AEMO)**
runs the NEM and publishes operational and market data through NEMWeb.

### What is `VIC1`?

`VIC1` is AEMO's identifier for the **Victorian region of the NEM**. It is a
market region, not a household, retailer, suburb, or individual power station.
In this project, each source value describes AEMO's regional dispatch-demand
estimate for Victoria at a particular five-minute interval.

The first version focuses on `VIC1` so the full pipeline can be developed and
audited for one region before it is extended to the rest of the NEM.

### What is being predicted?

The target is the:

> **Hourly mean of AEMO dispatch `TOTALDEMAND` estimates for `VIC1`, measured in
> megawatts (MW).**

AEMO publishes one `TOTALDEMAND` estimate for every five-minute dispatch
interval. This project combines 12 consecutive estimates to create one hourly
value.

This target is operationally relevant, but it is not:

- a direct meter reading of all electricity used in Victoria;
- total underlying consumer demand;
- household electricity consumption;
- energy measured in megawatt-hours; or
- AEMO's separate “operational demand” product.

Among other details in AEMO's formal definition, `TOTALDEMAND` excludes rooftop
solar and non-scheduled generation. See
[`docs/aemo_schema_audit.md`](docs/aemo_schema_audit.md) for the full definition
and supporting sources.

---

## The forecast contract

The intended forecasting task is deliberately specific:

| Question | Version 1 decision |
|---|---|
| Region | Victoria (`VIC1`) |
| Forecast origin | 00:00 NEM market time each day |
| Forecast horizon | The next 24 hourly intervals |
| Target | Hourly mean of dispatch `TOTALDEMAND`, MW |
| Planned outputs | p10, p50, and p90 for every target hour |
| Primary predictors | Historical demand and calendar information |
| Initial models | Seasonal baselines followed by LightGBM |
| Evaluation | Rolling-origin backtesting—never a random split |

For example, a forecast issued at market midnight covers:

```text
horizon 1  →  00:00–01:00, labelled 00:00
horizon 2  →  01:00–02:00, labelled 01:00
...
horizon 24 →  23:00–00:00, labelled 23:00
```

The p10 and p90 forecasts will form a nominal 80% prediction interval. The
project will test whether that interval is actually calibrated rather than
assuming it is.

Strictly speaking, this is a **midnight-origin, next-24-hour forecast**. It is
not presented as an AEMO pre-dispatch or market day-ahead product.

---

## From five-minute records to an hourly target

AEMO's source timestamps mark the **end** of each five-minute interval. The
records stamped:

```text
00:05, 00:10, 00:15, ..., 01:00
```

are combined into a canonical hour represented as:

```text
[00:00, 01:00), labelled 00:00
```

The original AEMO timestamps are retained. This is an explicit normalisation of
interval notation, not a shift in the underlying hour.

A valid hourly target must contain all 12 expected five-minute endpoints,
exactly once. An hour with a missing, duplicated, non-finite, or quarantined
record is flagged and excluded from the model-ready dataset by default. Values
are never silently filled, clipped, or averaged over an incomplete hour.

### Intervention intervals

During an AEMO intervention, the source may contain two records for the same
region and interval:

- `INTERVENTION=1`: the physical or base-case target run;
- `INTERVENTION=0`: the intervention-pricing run.

The pipeline preserves both upstream but uses the physical run for the
forecasting target. It never averages the two or allows both into the same
hour.

---

## Time without the daylight-saving trap

The AEMO data used here follow fixed **Australian Eastern Standard Time (AEST,
UTC+10)**. NEM market time does not move forward or backward with Victorian
daylight saving.

Every NEM market day therefore contains:

```text
24 hours = 288 five-minute intervals
```

Even when clocks in Melbourne repeat or skip an hour, the NEM market-time data
remain continuous. The pipeline keeps three concepts separate:

1. the timestamp supplied by AEMO;
2. the fixed UTC+10 NEM market timestamp; and
3. an unambiguous timezone-aware UTC timestamp.

Tests cover Victorian daylight-saving boundaries. Future calendar features may
also include Melbourne civil time to represent human activity, but civil time
will never be used to reinterpret the source market intervals.

---

## Architecture

```text
┌──────────────────────────────┐
│     AEMO NEMWeb archives     │
│  monthly public data files   │
└──────────────┬───────────────┘
               │ download, hash, preserve
               ▼
┌──────────────────────────────┐
│      Immutable raw data      │
│  source manifest + SHA-256   │
└──────────────┬───────────────┘
               │ schema and quality audit
               ▼
┌──────────────────────────────┐
│ Canonical five-minute records│
│ UTC + market time + flags    │
└──────────────┬───────────────┘
               │ physical-run selection
               │ endpoint normalisation
               ▼
┌──────────────────────────────┐
│     Validated hourly target  │
│ Parquet + quality reporting  │
└──────────────┬───────────────┘
               │ point-in-time features (2 profiles)
               ▼
┌──────────────────────────────┐
│ Rolling-origin backtesting   │
│ baselines → LightGBM         │
└──────────────┬───────────────┘
               │ p10 / p50 / p90
               ▼
┌──────────────────────────────┐
│ API, monitoring and dashboard│
│            planned           │
└──────────────────────────────┘
```

### Design rules

1. **Time semantics come before modelling.** An unnoticed one-hour shift can
   invalidate an otherwise sophisticated forecast.
2. **No future information.** Every feature must have been available at its
   historical forecast origin.
3. **Baselines first.** Machine learning must beat credible previous-day and
   previous-week forecasts.
4. **Raw data remain raw.** Source archives are never edited in place.
5. **Every result has provenance.** Configurations, hashes, code state, inputs,
   predictions, and metrics belong to the run that produced them.
6. **Failures remain visible.** Missing data, schema changes, and fallbacks are
   reported rather than hidden.

---

## Current status

The data foundation is complete through the hourly-target stage.

| Component | Status |
|---|---|
| Forecast and target contract | ✅ complete |
| Official AEMO downloader and immutable raw snapshot | ✅ complete |
| Schema, timestamp, and intervention audit | ✅ complete |
| Canonical five-minute normalisation | ✅ complete |
| Hourly VIC1 target and quality reporting | ✅ complete; approved 2026-10-16 (tag `data-contract-v1`) |
| Point-in-time feature builder (two availability profiles) | ✅ complete; approved 2026-10-16 (tag `feature-contract-v1`) |
| Seasonal forecasting baselines (development backtest) | ✅ complete; under review |
| LightGBM point and quantile models | 🔜 planned |
| Frozen final evaluation | 🔜 planned |
| FastAPI service and monitoring | 🔜 planned |
| Dashboard | 🔜 optional |

### Current data result

The locked source snapshot covers January 2022 through September 2026:

| Result | Value |
|---|---:|
| Official monthly AEMO archives | 57 |
| Compressed source data | ~320 MB |
| Selected VIC1 five-minute intervals | 499,392 |
| Complete hourly targets | 41,616 |
| Incomplete hourly targets | 0 |
| Coverage | 2022-01-01 00:00 to 2026-09-30 23:00 market time |

A public lock manifest records the URL, filename, byte size, SHA-256 hash,
report version, and archive member for every source file. The data themselves
are downloaded locally and are not committed to Git.

These are **data-pipeline results, not forecasting results**. No model
performance is claimed yet.

---

## Quickstart

### Prerequisites

- Python 3.11
- `make`
- Internet access for AEMO downloads
- Approximately 3 GB of free space for the full historical workflow

### Install and run the offline checks

```bash
git clone https://github.com/m-golzari/nem-demand-forecasting.git
cd nem-demand-forecasting

make install
.venv/bin/nem-forecast validate-config
make check
```

`make check` runs formatting, linting, strict type checks, offline tests, and
configuration validation.

### Try the seven-day source audit

```bash
.venv/bin/nem-forecast aemo-download --dry-run
.venv/bin/nem-forecast aemo-download
.venv/bin/nem-forecast aemo-audit
.venv/bin/nem-forecast aemo-normalise
```

The sample workflow downloads one official monthly archive and analyses a
seven-day `VIC1` window.

### Rebuild the full historical hourly target

The explicit approval flag prevents an accidental large download.

```bash
# Preview and then retrieve the historical snapshot
.venv/bin/nem-forecast aemo-snapshot \
  --dry-run --owner-approved-large-download
.venv/bin/nem-forecast aemo-snapshot \
  --owner-approved-large-download

# Audit and lock the exact input snapshot
.venv/bin/nem-forecast aemo-historical-audit \
  --snapshot <snapshot_id>
.venv/bin/nem-forecast aemo-snapshot-lock \
  --snapshot <snapshot_id>

# Construct and independently validate the hourly target
.venv/bin/nem-forecast build-hourly --run-id <new_run_id>
.venv/bin/nem-forecast validate-data --run-id <run_id>

# Build point-in-time features for both availability profiles, then reconcile in DuckDB
.venv/bin/nem-forecast build-features --run-id <new_run_id>
.venv/bin/nem-forecast validate-features --run-id <run_id>

# Development backtest of the seasonal baselines (refuses final-holdout origins)
.venv/bin/nem-forecast backtest-dev --run-id <new_run_id>
```

Raw data, processed data, logs, and run artefacts are intentionally ignored by
Git. Portable source-lock metadata remains in the repository so the exact input
snapshot can still be identified.

---

## Data quality and reproducibility

The pipeline checks more than whether a CSV can be opened. It verifies:

- archive identity, byte size, member name, and SHA-256 hash;
- expected regions and source columns;
- schema changes across archive versions;
- contiguous five-minute coverage;
- market-time and UTC conversion;
- dispatch-period numbering;
- duplicate and intervention combinations;
- missing, non-finite, and unexpected demand values;
- the 12 exact endpoints required for every hour;
- source-to-hour row reconciliation; and
- output hashes and date coverage.

The input is best understood as a **2026 retrieval snapshot of final historical
records**. Some older records have `LASTCHANGED` timestamps many hours after the
original interval. The archive does not reveal every value a forecaster would
have seen in real time. This limitation is documented rather than hidden.

Future feature construction will use a conservative one-hour availability
buffer in the primary experiment. That buffer is a modelling assumption—not a
claim that AEMO always publishes data with a one-hour delay—and it cannot
reconstruct values that were revised later.

---

## Planned modelling workflow

### 1 · Point-in-time features (built; under review)

Each row is keyed by forecast origin, target hour and horizon. The full design
table has 41,616 rows per profile, and no row is dropped. Features are built
twice, under two availability profiles:
- `primary_operational`: the latest usable source interval ends one hour
  before the origin;
- `sensitivity_zero_lag`: it ends at the origin. This profile is for
  sensitivity analysis only.

Every historical value records the latest five-minute source endpoint it used.
Each build checks that this endpoint is no later than the profile cutoff. It
also re-derives every value with a separate builder that only receives data up
to the cutoff, and reconciles lags and windows in DuckDB. See
[`docs/feature_registry.md`](docs/feature_registry.md) and
`docs/phase_reports/phase_3.md`.

Features: previous-day, two-day and previous-week demand; recent 24-hour and
168-hour statistics; recent same-hour summaries; market-time and Victorian
civil-time calendars, including daylight saving and public holidays. No model
has been trained or evaluated.

Observed future weather will not be used in the primary historical backtest. If
explored, it will be labelled as a realised-weather oracle, not an operational
forecast.

### 2 · Baselines before machine learning (development backtest done; under review)

The baselines are:
- previous-day same-hour demand (structurally unavailable at horizon 24 under
  the one-hour availability buffer, and never substituted);
- previous-week same-hour demand;
- a four-week same-weekday mean;
- a day/week hybrid with a fold-fitted weight.

They were compared over 12 monthly development folds, July 2025 to June 2026,
using MAE, WAPE, pinball loss and interval coverage. Comparisons use identical
rows only, and coverage is reported alongside, so a missing forecast can never
improve a ranking. Quantiles come from residuals of earlier, already-observed
outcomes. The final holdout (July to September 2026) is locked: the
development command refuses it.

Development results, which are not a claim about future performance, are in
`docs/phase_reports/phase_4.md`.

### 3 · Probabilistic LightGBM forecasts

LightGBM will produce p10, p50, and p90 estimates. Rolling-origin evaluation
will report MAE, RMSE, WAPE, bias, peak-period error, quantile loss, interval
coverage and width, and performance across horizons, hours, seasons, and demand
levels.

The final holdout period will remain untouched until the model and evaluation
rules have been frozen.

---

## Repository layout

```text
.
├── configs/
│   ├── forecast.yaml                 # version-1 forecast contract
│   ├── aemo_source.yaml              # source and snapshot settings
│   ├── feature_availability.yaml     # operational/sensitivity profiles
│   ├── features.yaml                 # registered feature set v1
│   ├── backtest.yaml                 # frozen Phase 4 protocol
│   └── data_snapshots/               # portable source lock manifests
├── docs/
│   ├── forecast_specification.md     # origin, horizon, and target rules
│   ├── aemo_schema_audit.md          # source semantics and audit evidence
│   ├── data_source_register.md       # documentation and provenance
│   ├── feature_registry.md           # feature availability contract
│   ├── decisions.md                  # project decision log
│   └── phase_reports/                # commands and outcomes by phase
├── src/nem_forecast/
│   ├── ingestion/                    # NEMWeb retrieval and source parsing
│   ├── validation/                   # schema and quality audits
│   ├── target/                       # hourly target and reconciliation
│   ├── features/                     # point-in-time features and leakage checks
│   ├── models/                       # seasonal baselines (ML planned)
│   ├── evaluation/                   # folds, calibration, metrics, backtest
│   ├── monitoring/                   # monitoring (planned)
│   └── serving/                      # prediction service (planned)
├── tests/                            # offline and marked integration tests
├── data/                             # local data; Git-ignored
├── results/runs/                     # local run artefacts; Git-ignored
├── CLAUDE.md
├── IMPLEMENTATION_PLAN.md
├── pyproject.toml
└── Makefile
```

---

## Known limitations

- `TOTALDEMAND` is a dispatch estimate, not total measured Victorian
  consumption.
- Historical archives contain final snapshot values, not a full reconstruction
  of what was visible at every past forecast origin.
- The project currently covers only `VIC1`.
- No weather information is included in the primary model design at this stage.
- No forecasting model has yet been trained or evaluated.
- This is a portfolio project, not a system operated by AEMO, an energy
  retailer, or a network business.

These constraints are part of the project, not footnotes to hide. A useful
forecasting system should make the limits of its evidence as visible as its best
metric.

---

## Contributing

Issues and pull requests are welcome, particularly around AEMO source
semantics, time-series leakage, probabilistic evaluation, and clear
communication of market data.

Please read [`CLAUDE.md`](CLAUDE.md) and
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) before making substantial
changes. The project is developed in gated phases so that data and time
assumptions are settled before model complexity is added.

---

## Licence and data

The project source code is available under the [MIT License](LICENSE).

AEMO and other external datasets are not included in this repository. They
remain subject to their providers' terms and are downloaded into local,
Git-ignored directories. This project is independent of AEMO and is not
endorsed by AEMO or any electricity-market participant.

<div align="center">

<sub>Good forecasts begin with honest timestamps, clear targets, and baselines
that are difficult to beat.</sub>

</div>
