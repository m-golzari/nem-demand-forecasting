# Phase 1B report: historical snapshot ingestion and audit

Date: 2026-10-14 · Status: **complete; awaiting owner approval of the
historical audit.** Phase 2 aggregation has not started. No weather, features,
models, API or dashboard work has been done.

Detailed findings: `docs/aemo_schema_audit.md` §10.

## Owner decisions applied

| Decision | Implementation |
|---|---|
| A-01 endpoint normalisation | Documented: source support `(00:00, 01:00]` (records 00:05…01:00) is represented canonically as `[00:00, 01:00)` labelled 00:00. Original stamps are preserved. Boundary test added |
| D-01 target wording | “Hourly mean of AEMO dispatch `TOTALDEMAND` estimates for VIC1”, with the definition and exclusions stated prominently in the forecast specification, data source register and README |
| D-06 interventions | Flag semantics confirmed from current AEMO documentation (*Intervention Pricing Methodology* v2.3, effective 3 June 2024, §3.3: base case target run = `Intervention=1`, pricing run = `Intervention=0`). The rule is implemented in `classify_primary_records`: one ordinary record is retained; a physical + pricing pair selects the physical row and records the difference; anything else is quarantined. Never both rows; never averaged |
| D-07 availability | `configs/feature_availability.yaml`: `primary_operational` uses a 1 h buffer (modelling assumption); `sensitivity_zero_lag` is labelled “SENSITIVITY ONLY”. `contract.latest_usable_interval_end` is added. The validator prevents the profiles being confused |
| D-08 snapshot | 57 archives 2022-01 → 2026-09 into git-ignored `data/raw`; history end frozen at 2026-09-30 (`configs/forecast.yaml`, `configs/aemo_source.yaml`) |

## Preconditions

- **Re-read:** `CLAUDE.md`, `IMPLEMENTATION_PLAN.md`, the forecast
  specification, the decisions log and the schema audit.
- **Storage:** the HPC project quota tool reported 427 of 502 GB used,
  so **75 GB is free**, above the 3 GB requirement. File quota: 1.35 M of 2 M.

## Commands and outcomes

| Step | Command | Outcome |
|---|---|---|
| Dry run | `nem-forecast aemo-snapshot --dry-run --owner-approved-large-download` | 57 months, 57 files, 319,479,933 bytes, 1 already present. Plan (every URL, month, expected filename, destination) saved to `data/raw/aemo/mmsdm/DISPATCHREGIONSUM/snapshots/…plan.2026-10-14T020537Z.json` (git-ignored) |
| Download | SLURM job **32942939**: `nem-forecast aemo-snapshot --owner-approved-large-download` | COMPLETED 0:0, 50 s. `OK: snapshot DISPATCHREGIONSUM_2022-01_2026-09_retrieved20261014 complete \| files 57 \| bytes 319479933 \| status {'downloaded': 56, 'already_present_verified': 1}`. 0 retry warnings, 0 leftover `.partial`/temp files |
| Audit | SLURM job **32943194**: `nem-forecast aemo-historical-audit --snapshot DISPATCHREGIONSUM_2022-01_2026-09_retrieved20261014` | COMPLETED 0:0, 86 s, max RSS 2.1 GB. `OK: 57 files, 2514395 rows \| schema changes 4 \| VIC1 intervention intervals 3487 \| quarantined rows 0`. Exit 0 (it would exit 2 and stop if any interval were quarantined) |
| Gate | SLURM job **32943806** (`scratch/phase1b_checks.sbatch`) | COMPLETED 0:0, 31 s; results below |

Gate results (job 32943806):

| Check | Outcome |
|---|---|
| `pip check` | No broken requirements |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 38 files already formatted |
| `mypy` (strict) | Success: no issues found in 19 source files |
| `pytest --cov=nem_forecast` (offline) | **139 passed**, 3 deselected (network/integration), coverage 90% |
| `pytest -m integration` (bounded historical audit: 10 archives around each schema change + June 2022 + 2026-09) | **2 passed** |
| `nem-forecast validate-config` | OK |
| `nem-forecast aemo-normalise --force` (Phase 1 window, now with selection columns) | 2016 rows, `selection_status_counts {'ordinary_single': 2016}` |

During development, 5 existing tests failed after the stricter download
validation, because the synthetic payload sizes no longer matched the fixed
listing size. Those tests were rewritten with size-correct listing fixtures.
All failures occurred before the final gate.

## File and count reconciliation

| Item | Value |
|---|---|
| Months planned / downloaded / verified | 57 / 57 / 57 (2022-01 … 2026-09, contiguous) |
| Bytes (sum of files = sum of listings) | 319,479,933 |
| Rows across all files | 2,514,395 |
| Expected intervals per region (1,734 days × 288) | 499,392; observed 499,392 in each of 5 regions |
| Ordinary single records | 2,479,525 (interval, region) groups |
| Intervention pairs | 3,487 intervals × 5 regions = 17,435 groups → 17,435 physical selected + 17,435 pricing not selected |
| Check | 2,479,525 + 2 × 17,435 = 2,514,395 ✓ |
| Primary records per region | 499,392 = one per interval |
| Quarantined | 0 |
| Last interval end | 2026-10-01 00:00 (+10:00), so coverage runs through the frozen end month 2026-09 |

## Schema changes

| From → to | Report version | Change |
|---|---|---|
| 2024-04 → 2024-05 | 5 → 8 | +11 columns |
| 2024-07 → 2024-08 | 8 → 8 | Column order changed (with the archive naming change) |
| 2025-03 → 2025-04 | 8 → 8 | +2 columns |
| 2025-05 → 2025-06 | 8 → 9 | Version bump only |

There were no removals, and all required columns are present throughout.

## Intervention findings (VIC1)

- **Scope:** 3,487 intervention intervals in 12 episodes (2022-02, 2022-06,
  2022-07, 2022-11, 2023-02, 2024-11). 2,493 intervals fall in the
  2022-06-14 → 2022-06-23 episode, and 297 canonical hours are touched.
- **Physical − pricing `TOTALDEMAND`:**
  - identical in 3,448 intervals;
  - 39 non-zero, all between 2022-06-14 14:10 and 2022-06-16 06:20, with a
    maximum |diff| of 19.97 MW and a mean of −0.07 MW;
  - mirrored by TAS1 (VIC1 + TAS1 sum ≤ 0.4 MW);
  - NSW1, QLD1 and SA1 differences are exactly 0.

## Unresolved anomalies (owner review)

1. **SA1 non-positive `TOTALDEMAND`:** 1,201 records (min −311.1 MW). Not
   VIC1; flagged, not modified.
2. **Late `LASTCHANGED` rewrites:** in 2022–2024, up to about 63 h after the
   interval (clusters in 2022-06 and 2022-11). The snapshot holds only
   last-changed values, so what was visible in real time is **not
   established**. This is a backtest-realism limitation that D-07's 1 h buffer
   does not cover.
3. **The 39 VIC1 physical ≠ pricing intervals:** handled by D-06. The cause
   (Basslink flow/loss terms) is an inference, not verified.
4. **The external context of the June 2022 episode** is not established from
   the sources consulted.

None of these triggered a stop condition. There was no filename ambiguity,
missing month, invalid archive, hash inconsistency, unsupported schema, or
quarantined interval.

## Files changed

| Kind | Files |
|---|---|
| New source | `src/nem_forecast/ingestion/snapshot.py`, `src/nem_forecast/validation/historical_audit.py` |
| Changed source | `ingestion/nemweb.py` (schema/zip inspection, retrying stream, resume with orphan verification, atomic read-only manifests), `ingestion/aemo_normalise.py` (D-06 classification), `provenance.py` (`write_json_atomic`), `contract.py` (endpoint-normalisation doc, `latest_usable_interval_end`), `config.py` (`HistoricalSnapshotConfig`, availability profiles), `cli.py` (`aemo-snapshot`, `aemo-historical-audit`) |
| Config | `configs/aemo_source.yaml` (`historical_snapshot`), `configs/feature_availability.yaml` (new), `configs/forecast.yaml` (end date frozen), `pyproject.toml` (`integration` marker) |
| Tests | New: `test_snapshot.py`, `test_selection_rule.py`, `test_historical_audit.py`, `test_availability.py`, `test_integration_snapshot.py` (`@integration`). Updated: `aemo_fixtures.py`, `test_nemweb.py`, `test_cli_aemo.py`, `test_aemo_normalise_audit.py`, `test_config.py` |
| Docs | `forecast_specification.md` (rewritten), `aemo_schema_audit.md` (§5 rewritten, §10 added), `decisions.md`, `data_source_register.md`, `feature_registry.md`, `README.md`, this report |
| Not committed (git-ignored) | All raw archives, manifests, listings, plans, snapshot manifests, interim audit outputs, SLURM scripts and logs |

## Claim status

| Claim | Basis |
|---|---|
| `INTERVENTION=1` physical, `0` pricing | Verified from documentation (D10 §3.3) |
| 57/57 months, complete coverage, 0 duplicates, 0 quarantined | Raw data, fresh runs (jobs 32942939, 32943194) |
| Schema changes as listed | Raw data, fresh run |
| VIC1 physical − pricing differences | Raw data, fresh run |
| Basslink cause of VIC1/TAS1 differences | Inference |
| Real-time availability / original-publication values | Not established |
| All checks pass | Fresh run (job 32943806) |
