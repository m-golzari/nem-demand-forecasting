# Implementation Plan — NEM Demand Forecasting and Monitoring Platform

Read `CLAUDE.md` first. Implement one phase at a time. Satisfy each acceptance
gate before proceeding to large downloads, final evaluation, or deployment.

## Session protocol

1. Read `CLAUDE.md` and this plan.
2. Inspect repository tree and `git status`.
3. Identify the first incomplete phase and its acceptance criteria.
4. Propose the smallest coherent work unit.
5. Implement, test, and update its phase report.
6. Do not only describe work that can safely be implemented now.
7. Stop at owner-decision gates.

## Phase 0 — Contract and scaffold

Create the required directories, `pyproject.toml`, Ruff/pytest configuration,
`.gitignore`, `.env.example`, Makefile, package/subpackages, validated YAML
configuration, project-root path handling, README, and:

- `docs/forecast_specification.md`
- `docs/data_source_register.md`
- `docs/feature_registry.md`
- `docs/decisions.md`
- `docs/phase_reports/phase_0.md`

Use Python 3.11. Suggested dependencies, added only as needed: pandas, pyarrow,
duckdb, pydantic, pydantic-settings, PyYAML, Typer, httpx, tenacity, holidays,
scikit-learn, lightgbm, FastAPI, uvicorn; development: pytest, pytest-cov, Ruff,
mypy, pandas-stubs.

Freeze VIC1, TOTALDEMAND, hourly mean MW, 00:00 NEM-time origin, next 24 hours,
and demand/calendar primary model. Add config/path tests.

**Gate:** editable install, import, lint, and tests pass; a CLI validates/prints
config; forecast contract has no unresolved target/unit/horizon issue.

## Phase 1 — Seven-day AEMO reconnaissance

Research current official AEMO source and metadata. Record authoritative links.
Choose direct NEMWeb, nemosis, or an adapter and document trade-offs. Implement a
source adapter, download only seven days, retain raw archives/responses, and
hash them. Manifest URL, parameters, retrieval UTC time, bytes, hash, and path.

Audit actual columns/dtypes, regions, timestamps, duplicate/publication fields,
spacing, nulls, and demand range. Normalise to canonical interim Parquet with
source timestamp, market timestamp, UTC timestamp, region, demand MW, necessary
publication metadata, and quality flags. Implement duplicate resolution only
after its semantics are documented. Add a CLI, offline fixtures, marked network
test, `docs/aemo_schema_audit.md`, and phase report.

**Gate:** provenance, timezones, counts, duplicate policy, VIC1 filter, and units
are verified; no critical unexplained shift/schema issue.

**Owner gate:** report estimated multi-year volume and ask before downloading it.

## Phase 2 — Hourly target and quality report

Establish source interval start/end semantics. Implement time-weighted hourly
mean MW, interval count/coverage, incomplete-hour flags, and configurable
exclusion. Never sum MW as MW. Create JSON and Markdown quality reports.

Test complete/missing/duplicate/irregular intervals, UTC boundary, and DST dates.
Write processed Parquet and DuckDB views. Add `build-hourly` and `validate-data`
CLI commands and phase report.

**Gate:** manual toy calculations match; counts reconcile; incomplete hours are
explicit; tests are offline.

## Phase 3 — Point-in-time feature dataset

Implement target calendar and Victorian holiday features. Define rows by
forecast origin, target timestamp, and horizon. Add demand lags (24, 48, 168h)
and historical same-hour/rolling summaries ending no later than origin. Track
latest source timestamp used, populate feature registry, and add leakage tests.
Add `build-features` CLI and dataset report.

**Gate:** all features registered; tests prove source times do not exceed origin;
complete origins have 24 ordered horizons unless explicitly filtered.

## Phase 4 — Baselines and rolling backtest

Implement one forecaster interface; day, week, same-hour/day-type, and weighted
baselines; MAE, RMSE, aggregate WAPE, bias, peak MAE, pinball loss, coverage,
width, and crossing. Implement configured expanding rolling folds and prevent
train/validation overlap. Save every prediction and metric. Add plots and a
baseline report. Freeze a later final holdout but do not evaluate it.

**Gate:** metric tests and chronology pass; models compare on identical rows or
differences are disclosed; one command reproduces development results.

## Phase 5 — Weather oracle (optional but useful)

Implement an Open-Meteo historical adapter for Melbourne with cached raw
responses, retries, manifests, attribution, UTC normalisation, validation, and
offset tests. Put observed weather in a separate `realised_weather_oracle`
configuration. Never enable it in the operational config. Document what is
required for archived forecast-weather vintages.

**Gate:** no unexplained join offset; operational/oracle configurations and
reports cannot be confused.

## Phase 6 — LightGBM point and quantile models

Add LightGBM through the common interface. Train demand/calendar point and
p10/p50/p90 models. Fit processing per fold. Use only a bounded development-fold
search and record trials/seeds. Save feature names, dates, versions, parameters,
and hashes. Report crossing before and after any documented correction. Compare
with all baselines on identical rows and write model card/report. Optional
weather-oracle results remain separate.

**Gate:** save/load consistency; reproducible development results; uncertainty
coverage/width and error slices reported; holdout untouched.

**Owner gate:** present frozen candidate and ask before final evaluation.

## Phase 7 — Frozen final evaluation

Write a pre-evaluation manifest containing git state, data hashes, features,
model, holdout dates, metrics, and seeds. Run frozen baselines and model once on
identical final targets. Report all metrics/slices and block-bootstrap paired
uncertainty if feasible. Do not tune after viewing results. Write final report
and update README with run IDs.

**Gate:** manifest matches run; holdout isolation is verified; claims trace to
saved predictions/metrics, including failures.

## Phase 8 — CLI and manifests

Harden commands for validation, ingestion, transformation, features, backtest,
training, forecast, and monitoring. Add dry runs for substantial writes,
structured logs, nonzero failure exits, non-overwriting run IDs, freshness/schema
checks, and a fixture end-to-end pipeline.

**Gate:** clean checkout can reproduce the demo; failures cannot look successful;
manifests capture code/config/inputs/outputs.

## Phase 9 — FastAPI and Docker

Define request/response models and `/health`, `/model-info`, `/forecast`. Load an
approved artefact at startup; validate region, aware origin, horizon, freshness,
and feature availability. Return model/run/version/quantiles/fallback. Implement
seasonal-naive fallback. Add API tests and a non-root Docker image without raw
data or secrets.

**Gate:** success/error/fallback tests pass; no training occurs in API; Docker
build and health check pass where available.

## Phase 10 — Monitoring and runbook

Persist issued forecasts separately, reconcile matured predictions with actuals,
and monitor availability, freshness, recent error/bias, peak underprediction,
coverage/width, feature shift, and fallback use. Keep pipeline failures separate
from model failures. Create provisional thresholds and `docs/runbook.md` for
source delay, schema change, missing features, stale model, high error, poor
coverage, and API failure. Define but do not blindly automate retraining.

**Gate:** fixture monitoring works; failure classes and recovery are explicit.

## Phase 11 — CI and portfolio presentation

Configure offline GitHub Actions for lint, tests, type checks, and fixture E2E.
Optionally add manual/low-frequency network smoke tests; never run large history
or retraining in CI. Add architecture/data-flow diagrams, licensing, source
attribution, leakage controls, reproducible measured results, API example,
monitoring, limitations, and evidence-based CV bullets. Add screenshots only
after results reproduce.

**Gate:** clean CI passes; README commands work; quantitative claims trace to run
IDs; no secrets, huge files, unlicensed data, or invented results.

## Phase 12 — Optional extensions

Only after core reliability: Streamlit, other regions, multiple weather sites,
archived forecast weather, conformal calibration, hierarchical forecasts, price
forecasting, battery optimisation, or cloud orchestration. Each needs a design
note, leakage review, baseline, and acceptance criterion.

## First prompt to Claude Code

> Read `CLAUDE.md` and `IMPLEMENTATION_PLAN.md` fully. Inspect the repository and
> git status. Begin Phase 0 only. Summarise the fixed forecast contract, identify
> contradictions or missing decisions, and list exact files to change. Then
> implement the smallest complete Phase 0 scaffold, run installation/import/lint/
> tests, and write `docs/phase_reports/phase_0.md` with exact commands and
> outcomes. Do not download energy data, implement later phases, commit, or push.
> If an important contract decision is unresolved, ask one focused question.

## Gate prompts

Before multi-year download:

> Show the seven-day schema audit, timestamp/duplicate conclusions, row-count
> reconciliation, provenance, estimated volume, and all checks. Do not download
> multiple years until I approve.

Before final holdout:

> Show the frozen model, development baseline comparison, feature registry,
> leakage-test evidence, holdout dates, and pre-evaluation manifest. Do not access
> holdout outcomes until I approve.

Before cloud work:

> Demonstrate local API, fixture E2E, Docker checks, fallback, and runbook. Propose
> minimum architecture and costs. Do not provision resources or request
> credentials until I approve.
