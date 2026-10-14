# NEM VIC1 demand forecasting (portfolio prototype)

This is a reproducible, leakage-safe portfolio data product. It forecasts the
next 24 hourly VIC1 demand values (hourly mean of AEMO `TOTALDEMAND`, MW) from
a daily 00:00 NEM-market-time origin, with p10/p50/p90 outputs. It is not
operated by AEMO or any retailer.

**Status:** Phase 1 complete (seven-day AEMO reconnaissance; see
`docs/aemo_schema_audit.md`). No multi-year data and no models yet. See
`IMPLEMENTATION_PLAN.md` and `docs/phase_reports/`.

The target is the hourly mean of AEMO dispatch `TOTALDEMAND`. That is NEMDE's
end-of-interval forecast of scheduled demand, not metered consumption.

## Setup (Python 3.11)

```bash
make install                 # creates .venv and installs -e ".[dev]"
.venv/bin/nem-forecast validate-config
.venv/bin/nem-forecast show-config
make check                   # ruff + mypy + pytest + config CLI

# Seven-day AEMO sample (official NEMWeb MMSDM archive; ~4.6 MB)
.venv/bin/nem-forecast aemo-download --dry-run   # report URLs, sizes, destinations
.venv/bin/nem-forecast aemo-download             # hashed, manifested, never overwritten
.venv/bin/nem-forecast aemo-audit                # machine-readable schema/quality audit
.venv/bin/nem-forecast aemo-normalise            # canonical VIC1 interim Parquet
```

Tests run offline by default. Network tests are marked `network`; run them with
`make test-network`.

## Layout

| Path | Content |
|---|---|
| `src/nem_forecast/` | Package: `config`, `paths`, `contract`, `cli`, and stub subpackages for later phases |
| `configs/forecast.yaml` | Version-1 forecast contract (validated) |
| `docs/` | Forecast specification, data source register, feature registry, decisions, phase reports |
| `data/{raw,interim,processed}/` | Immutable raw inputs, then normalised data, then model-ready data (git-ignored) |
| `results/runs/<run_id>/` | Run artefacts (git-ignored) |
| `tests/` | Offline pytest suite |

## Licence and data

Project source code is released under the MIT License (`LICENSE`). External
datasets (AEMO NEMWeb/MMS, Open-Meteo, and others) are **not** included in this
repository and are governed by their providers' terms; the pipeline downloads
them locally into git-ignored `data/` directories with provenance manifests.
