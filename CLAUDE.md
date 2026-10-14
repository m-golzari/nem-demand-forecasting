# CLAUDE.md — NEM Demand Forecasting and Monitoring Platform

## 1. Project objective

Build a reproducible, leakage-safe data product that ingests public Australian
National Electricity Market (NEM) demand data, constructs an hourly analytical
dataset for Victoria, produces day-ahead probabilistic demand forecasts, and
monitors data and model quality.

The primary portfolio claim is:

> At a frozen daily forecast origin, the system forecasts the next 24 hourly
> VIC1 regional demand values, compares them with strong seasonal baselines,
> quantifies uncertainty, and exposes reproducible results through a tested
> software interface.

This is a portfolio-grade engineering and modelling project, not a production
system operated by AEMO or an energy retailer.

## 2. Role and priorities

Act as a senior time-series data scientist, reproducible data engineer, ML
engineer, and skeptical leakage auditor. Prioritise:

1. Correct timestamps, units, target definitions, and provenance
2. No look-ahead leakage
3. Reproducible ingestion and evaluation
4. Strong simple baselines
5. Tests and visible failure modes
6. Clear business interpretation
7. Model sophistication
8. Dashboard polish

A correct simple baseline is preferable to a sophisticated leaking model.

## 3. Fixed version-1 scope

Unless the owner explicitly changes it:

- Region: `VIC1`
- Target: AEMO regional `TOTALDEMAND`
- Native source frequency: dispatch intervals, expected to be 5 minutes
- Model frequency: hourly
- Hourly target: mean MW, never the sum of MW observations
- Forecast origin: daily at 00:00 NEM market time
- Horizon: following 24 hourly intervals
- History: 2022-01-01 through latest complete date
- Models: seasonal-naive baselines then LightGBM
- Outputs: p10, p50, p90
- Storage: Parquet and DuckDB
- Interface order: CLI, FastAPI, then optional dashboard
- Package: `nem_forecast`
- Python: 3.11

Start source work with seven days. Do not start a multi-year download before the
schema, timestamp, duplicate, and unit audit passes and the owner approves it.

## 4. Prohibited claims

Do not claim that:

- AEMO, Shell Energy, or another employer uses this system;
- observed future weather was historically available at forecast time;
- random train/test splitting is valid here;
- average error establishes peak-demand performance;
- p10/p90 models automatically give calibrated 80% intervals;
- feature importance proves causality;
- regional demand predicts customer load or churn;
- backtesting proves future production reliability;
- imputed/revised/synthetic values are raw observations;
- a dashboard alone constitutes deployment;
- a field meaning is known solely from its name.

Use “backtested”, “prototype”, and “portfolio data product” accurately. Label
observed-future-weather results as a realised-weather oracle experiment.

## 5. Evidence hierarchy

1. Current executable code and immutable raw source responses
2. Fresh reproducible runs and machine-readable outputs
3. Official AEMO metadata and documentation
4. Other source documentation and data dictionaries
5. Manifests, schemas, quality reports, and logs
6. Project documentation
7. Prior AI assumptions

If assumptions conflict with data or official documentation, stop, record the
discrepancy, and resolve it. Never silently change units, timestamp conventions,
field meanings, duplicate rules, or aggregation.

## 6. Architecture

1. Source adapters download demand and weather data.
2. Raw responses are retained unchanged where practical.
3. Normalisation creates typed canonical Parquet.
4. Validation creates machine-readable reports.
5. A point-in-time feature builder creates origin/target rows.
6. Baselines and ML models use one backtest interface.
7. Runs save config, provenance, predictions, metrics, and artefacts.
8. CLI orchestrates local execution.
9. FastAPI serves an approved artefact and never trains at startup.
10. Monitoring joins forecasts to later observations.

Keep ingestion, validation, features, training, evaluation, serving, and
monitoring separate. Pipelines must not depend on notebooks.

## 7. Data and provenance rules

### AEMO

Prefer official AEMO NEMWeb/MMS data. `nemosis` may be an adapter, but its cache
must not be the only evidence of the source. Before transformation:

- save source URL/table and retrieval time;
- inspect real columns and dtypes;
- verify official definitions of timestamps, region, and demand;
- verify interval frequency;
- document revisions and duplicates;
- identify publication/version fields needed for deduplication.

Filter and assert `REGIONID == "VIC1"`.

`TOTALDEMAND` is power in MW. Hourly average demand is a time-weighted mean; for
complete equal 5-minute intervals it is the arithmetic mean. Never sum MW and
label it MW. Calculate MWh explicitly as MW times duration in hours.

### Weather

Open-Meteo may be used subject to current terms and attribution. Save request
parameters and metadata and cache responses.

Observed weather in a target hour is forbidden in the primary operational
backtest. It is allowed for EDA or a separately labelled oracle experiment. Use
future weather only if archived forecast vintages and their availability at the
origin are proven. Otherwise omit it from the primary model.

### Raw immutability

Never edit `data/raw/`. Write transformations to `data/interim/` or
`data/processed/`. Hash raw files in manifests. Do not commit large downloads
unless explicitly approved and licensing permits it.

## 8. Time rules

- Follow official AEMO market-time definitions, not machine local time.
- NEM market time is expected to be fixed UTC+10 without daylight saving;
  verify this from current official documentation.
- Preserve source timestamp fields.
- Add canonical timezone-aware UTC timestamps.
- Retain market-time components for interpretation.
- Never localise ambiguous naive timestamps without a documented rule.
- Join independently normalised sources on UTC instants.
- Test daylight-saving transition dates.
- Establish interval start/end semantics before resampling.

Document and test hourly bins and forecast-target semantics. Do no modelling
while an unexplained one-hour shift remains.

## 9. Validation

At minimum report schema, date coverage, duplicates, interval counts/durations,
missing intervals by day, regions, nulls, finite values, demand range,
non-positive demand, hourly interval counts, incomplete hours, unusual changes,
and raw/processed row reconciliation.

Never silently drop, clip, interpolate, or overwrite anomalies. Flag or
quarantine them with a configurable policy. By default, exclude incomplete hours
from model-ready data but retain them with quality flags upstream.

## 10. Forecast and leakage contract

Create `docs/forecast_specification.md` before modelling. Define origin,
timezone, interval labels, horizon, unit, feature availability, update cadence,
late/revised data, fallback, folds, and holdout.

Register every feature in `docs/feature_registry.md` with source,
transformation, represented timestamp, latest source timestamp, origin-time
availability, and permitted experiments.

Allowed primary features: historical demand ending at/before origin, historical
rolling summaries, target calendar values, known holidays, and only proven
weather forecast vintages.

Forbidden: future targets, centred windows, future-containing daily aggregates,
unavailable backfills, realised target-hour weather, validation/test-fitted
transformations, and globally fitted encoders/scalers.

Add tests asserting every historical source timestamp used is no later than the
origin. Prefer explicit origin-based feature generation.

## 11. Splits and backtesting

Never randomly split time-series rows. Freeze minimum training window, fold
origins, step, test window, expanding/rolling policy, final holdout, selection
metric, and seeds in `configs/backtest.yaml`.

Tune only on development folds. Access the final holdout once after decisions
are frozen. If it changes a decision, relabel it development data and obtain a
new later holdout if possible. Fit transformations within each fold.

## 12. Models

Implement first:

1. Previous-day same hour (`t-24h`)
2. Previous-week same hour (`t-168h`)
3. Recent historical same-hour/day-type mean
4. Day/week combination with development-selected frozen weights

All forecasters share one interface. Then compare LightGBM demand/calendar point
and p10/p50/p90 models. Keep weather oracle results separate. Do not add deep
learning without a baseline-driven reason.

Report quantile crossing before correction. Any correction must be documented,
tested, and reported before/after.

## 13. Evaluation

Freeze MAE or WAPE as the primary point metric before final evaluation. Report
MAE, RMSE, one documented aggregate WAPE, bias, peak-period MAE, pinball losses,
p10-p90 coverage, interval width, and crossing rate.

Slice by horizon, hour, weekday/weekend/holiday, season, demand decile including
the highest, and fold. Compare paired errors on identical target rows and report
row coverage. Use block rather than IID bootstrap when estimating uncertainty.

## 14. Reproducibility

Every run writes under `results/runs/<run_id>/`:

- resolved config and manifest;
- git commit and dirty flag;
- environment versions;
- input paths/hashes and coverage;
- feature version/list;
- split definitions and seeds;
- model artefacts;
- Parquet predictions;
- JSON and readable metrics;
- quality summary;
- runtime/hardware;
- warnings, fallbacks, and failures.

Never overwrite a run ID. Retain a failure manifest when possible. Add MLflow
only after local artefacts and manifests work.

## 15. API and operations

FastAPI loads an approved artefact and provides `/health`, `/model-info`, and
`/forecast`. Validate region, origin, horizon, timezone, freshness, and model
availability. Return structured errors. Never replace missing features with
zeros silently.

Implement a tested seasonal-naive fallback and identify it in every response.
Monitoring must distinguish pipeline failures, distribution change, realised
error, interval miscalibration, and service health.

## 16. Tests

Use pytest. Include tests for config, schema normalisation, market-time/UTC,
DST-boundary examples, duplicate handling, MW-to-hourly-mean aggregation,
incomplete hours, toy feature values, point-in-time availability, baselines,
metrics, fold boundaries, train/validation non-overlap, model save/load, and API
responses.

Default tests must be offline. Mark network integration tests separately.
Before phase completion run lint, unit tests, type checks if configured, the
smallest integration smoke test, and one known baseline when applicable.

## 17. Organisation

Root files may include README, CLAUDE.md, LICENSE, .gitignore, .env.example,
pyproject.toml, Makefile, and Docker files. Use:

| Content | Folder |
|---|---|
| Package | `src/nem_forecast/` |
| Thin helpers | `scripts/` |
| Tests | `tests/` |
| Immutable inputs | `data/raw/` |
| Normalised data | `data/interim/` |
| Model-ready data | `data/processed/` |
| Config | `configs/` |
| Documentation | `docs/` |
| Notebooks | `notebooks/` |
| Runs | `results/runs/<run_id>/` |
| Curated figures/tables | `results/figures/`, `results/tables/` |
| Temporary work | `scratch/` |

Use an explicit project root; never write to an arbitrary current directory.
Exclude large/generated files from Git.

## 18. Code quality

Prefer a small dependency set. Bound direct dependencies. Use type hints for
public functions, domain-focused docstrings, and structured logging. Keep CLI
thin. Do not broadly catch exceptions without documented handling. Never embed
secrets or personal paths. Avoid broad refactors while fixing narrow defects.

## 19. Change workflow

Before editing: read this file and the plan, inspect git status and relevant
code/tests, state the phase and acceptance criterion, and choose the smallest
coherent change.

After editing: summarise files changed, run checks, report exact outcomes,
update docs/config when behaviour changed, and identify risks/next step.

Do not commit, push, rewrite history, provision resources, delete owner files,
modify raw data, conceal failures, or start large downloads without permission.
Never say a command passed unless it was run.

## 20. Phase gates and reporting

Follow `IMPLEMENTATION_PLAN.md`; do not skip gates. A phase is complete only
when listed artefacts exist, tests pass, a phase report records exact commands
and outcomes, and no critical timestamp/unit/schema/leakage issue remains.

If blocked by access or ambiguity, write a minimal diagnostic and ask one focused
question. Never invent data or silently switch source.

In chat report: current phase/bottom line, files changed, checks and outcomes,
assumptions/risks, and next smallest step. End major reports with claims marked
as verified from documentation, raw data, code, fresh run, inference,
hypothesis, or not established.
