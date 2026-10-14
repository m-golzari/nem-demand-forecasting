# Phase 0 report: contract and scaffold

Date: 2026-10-14 · Status: **gate passed**. Open decisions remain for later
phases; none affects the target, unit, or horizon.

## Starting state

`nem-demand-forecast/` contained only `CLAUDE.md` and `IMPLEMENTATION_PLAN.md`.
`git status` gave `fatal: not a git repository`. No git operations were performed.

## Acceptance criterion (IMPLEMENTATION_PLAN.md, Phase 0)

Editable install, import, lint, and tests pass; a CLI validates/prints config;
the forecast contract has no unresolved target/unit/horizon issue.

## Files created

- Root: `pyproject.toml`, `.gitignore`, `.env.example`, `Makefile`, `README.md`
- Config: `configs/forecast.yaml`
- Package `src/nem_forecast/`: `__init__.py`, `py.typed`, `paths.py`
  (explicit root resolution), `config.py` (pydantic contract with `Literal`
  frozen fields), `contract.py` (origin and 24 target hours, UTC conversion),
  `cli.py` (`validate-config`, `show-config`), stub subpackages `ingestion`,
  `validation`, `features`, `models`, `evaluation`, `serving`, `monitoring`
- Tests `tests/`: `conftest.py`, `test_import.py`, `test_paths.py`,
  `test_config.py`, `test_contract.py`, `test_cli.py` (56 tests, all offline)
- Docs: `docs/forecast_specification.md`, `docs/data_source_register.md`,
  `docs/feature_registry.md`, `docs/decisions.md`, this report
- Directories with `.gitkeep`: `data/{raw,interim,processed}`,
  `results/{runs,figures,tables}`, `scratch`, `scripts`, `notebooks`
- Local only (git-ignored): `.venv/`, `scratch/phase0_checks.sbatch` and its log

## Commands and outcomes

Environment setup ran interactively on an HPC node:

```bash
python3.11 -m venv .venv                         # Python 3.11.7 (Anaconda3/2024.02-1)
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -e ".[dev]"      # Successfully installed ... nem-forecast-0.0.1
```

One preliminary lint finding (RUF007 in `tests/test_contract.py`, `zip` →
`itertools.pairwise`) was fixed before the formal run.

The formal checks ran as SLURM job **32936430** (`sbatch
scratch/phase0_checks.sbatch`, SLURM compute node,
COMPLETED 0:0, 8 s). Log: `scratch/phase0_checks_32936430.log`.

| Command | Outcome |
|---|---|
| `.venv/bin/python -m pip check` | `No broken requirements found.` exit 0 |
| `.venv/bin/python -c "import nem_forecast, nem_forecast.cli; ..."` | `0.0.1 .../src/nem_forecast/__init__.py` exit 0 |
| `.venv/bin/ruff check src tests` | `All checks passed!` exit 0 |
| `.venv/bin/ruff format --check src tests` | `19 files already formatted` exit 0 |
| `.venv/bin/mypy` (strict, `src`) | `Success: no issues found in 12 source files` exit 0 |
| `.venv/bin/pytest --cov=nem_forecast --cov-report=term-missing` | `56 passed in 0.43s`, coverage 97% exit 0 |
| `.venv/bin/nem-forecast validate-config` | `OK: .../configs/forecast.yaml \| VIC1 TOTALDEMAND hourly mean MW \| origin 00:00 UTC+10 \| horizon 24h \| quantiles [0.1, 0.5, 0.9]` exit 0 |
| `.venv/bin/nem-forecast show-config` | resolved JSON config and absolute project root, exit 0 |
| `validate-config` run from `/tmp` (foreign CWD) | same OK line; root resolved to the project, exit 0 |
| `nem-forecast validate-config --root /tmp` | `ERROR: /tmp is not a nem-forecast project root ...` exit **1** (expected) |

No network tests exist yet. The `network` marker is registered and deselected by default.

## What the tests establish

- Config: the repository YAML is valid. Changing region, field, unit,
  aggregation (`sum`), native interval, UTC offset, label convention, origin,
  horizon, quantiles, feature set, weather flag, or history start is rejected.
  Unknown keys, missing sections, and an end date before the start are
  rejected. Loaded config is immutable.
- Paths: the root is resolved independently of the CWD. Explicit root beats
  environment, and an invalid root fails loudly. Relative config paths resolve
  against the root. Path objects never create directories.
- Contract: origin = market midnight = 14:00 UTC previous day. There are 24
  contiguous left-closed hourly targets, all in UTC. Four Victorian DST
  transition dates still give 24 hours at a fixed offset. A guard test shows
  market time differs from `Australia/Melbourne` civil time in summer.
- CLI: success output and JSON. Invalid config or invalid root exits with code 1.

## Contradictions and missing decisions found

See `docs/decisions.md` for full entries.

1. “Day-ahead” (CLAUDE.md §1) vs a 00:00 origin whose h=1 starts immediately.
   Adopted A-08: next-24 h from market midnight; not an AEMO day-ahead product.
2. Hour labelling was unspecified. Adopted A-01: left-closed, start-labelled
   bins. Source-to-bin mapping depends on `SETTLEMENTDATE` semantics (O-02, Phase 1).
3. “Latest complete date” is not reproducible. Adopted A-04: frozen in the
   snapshot manifest.
4. Open for later phases: UTC+10 verification (O-01), source table and field
   definition (O-02), duplicate/intervention rule (O-03), adapter (O-04),
   publication latency at origin (O-05), calendar time basis (O-06), metric,
   folds, and holdout (O-07), baseline quantiles (O-08), no git repo (O-09),
   licence (O-10).

## Risks

- Manifests require a git commit and dirty flag (CLAUDE.md §14), but the
  directory is not a repository (O-09).
- A `lag_24h` at h=24 uses the hour ending exactly at the origin. That is
  legal under the contract but may be optimistic if publication latency is
  more than 0 (O-05).
- The UTC+10 market time and 5-minute native interval are assumptions,
  encoded as frozen values pending Phase 1 verification.

## Post-approval publication (2026-10-14)

The owner approved Phase 0 and the start-labelled hour convention (A-01). For
publication I added `LICENSE` (MIT; external data excluded), a licence section
in `README.md`, and extra `.gitignore` rules (SLURM outputs, databases, keys,
editor files). I then initialised git and re-ran the full gate (see below)
before the initial commit: SLURM job **32938623** (COMPLETED 0:0, 12 s)
reinstalled the editable package, then pip check, import, ruff check,
ruff format --check, mypy strict, pytest (56 passed, 97% coverage), and both
CLI commands passed. The invalid-root negative case exited 1 as expected. The
results were identical to job 32936430. The system date was checked and is 2026-10-14, so
no dates needed correcting.

## Next step

Phase 1: research official AEMO sources and metadata, then do a seven-day VIC1
reconnaissance download with provenance manifest and schema audit. Before that,
get owner confirmation on O-09 (git init).

## Claim status

| Claim | Basis |
|---|---|
| Install, import, lint, typecheck, tests, CLI pass | Fresh run (SLURM job 32936430) |
| Contract values (VIC1, TOTALDEMAND, MW mean, 00:00, 24 h, quantiles) | Owner documentation (CLAUDE.md), enforced in code |
| NEM market time = fixed UTC+10 | **Not established** (assumption; Phase 1) |
| 5-minute native interval, `TOTALDEMAND` meaning, interval-ending timestamps | **Not established** (Phase 1) |
| Start-labelled hourly bins | Implementer decision (A-01) |
