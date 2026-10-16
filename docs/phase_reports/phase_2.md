# Phase 2 report: canonical VIC1 hourly target and quality reporting

Date: 2026-10-14 · Status: **complete and approved by the owner (2026-10-16).**
The approved data contracts are tagged `data-contract-v1` (annotated tag on the
Phase 2 closeout commit).

**Two layers of checks.**
- `build-hourly` evaluates **16 pipeline-derived quality invariants**
  (`target/build.py::derived_checks`). Their expected values are derived from
  the configured window.
- `validate-data` runs **13 independent re-derivation checks**
  (`target/reconcile.py::reconcile_run`). These compare the pipeline outputs
  with a stdlib-csv re-read of the raw archives and a DuckDB re-derivation of
  selection, hour mapping and means.

All 29 passed.

The target is the **hourly mean of AEMO dispatch `TOTALDEMAND` estimates for
VIC1, in MW** (column `dispatch_totaldemand_hourly_mean_mw`). It is not
measured consumption, operational demand or MWh.

## Owner corrections to Phase 1B (applied first)

| Correction | Done |
|---|---|
| Public snapshot lock | `configs/data_snapshots/DISPATCHREGIONSUM_2022-01_2026-09_retrieved20261014.lock.json` (40,680 bytes, SHA-256 `25ebc30a8667af7b3d893cf1f16494a172ada2199a82b8e16dd3f540af9e6180`). For all 57 archives it records month, official URL, filename, bytes, SHA-256, retrieval UTC, listing timestamp, report version, expected archive member and its size/CRC. It contains no local paths, credentials or machine data, and is built by `nem-forecast aemo-snapshot-lock` with deterministic serialisation |
| Stale schema-audit text | `docs/aemo_schema_audit.md` now has a reading guide. The dated Phase 1 seven-day record (§3, §4, §6.1, §7.1) is separated from current conclusions (§2, §5, §6.2, §7.2, §9, §10). The “only one month / no intervention observed” limitation is restricted to Phase 1. The “intervention selection unresolved” wording is removed from current text |
| Retrospective scope | Recorded in §2 (`INTERVENTION` row), §9 and §10.3: AEMO *Intervention Pricing Methodology* v2.3 (effective 2024-06-03) is applied retrospectively to 2022–2024 archives, consistent with the observed structure. The D-06 rule is not reopened |

## Implementation

| Item | Location |
|---|---|
| Hourly construction (pure) | `src/nem_forecast/target/hourly.py`: `build_hourly`, `model_ready` |
| Pipeline (I/O, manifest) | `src/nem_forecast/target/build.py`: `run_phase2`; CLI `nem-forecast build-hourly` |
| Independent reconciliation | `src/nem_forecast/target/reconcile.py`, `target/sql/vic1_hourly_reconcile.sql`; CLI `nem-forecast validate-data` |
| Reproducible DuckDB views | `src/nem_forecast/target/sql/vic1_hourly_views.sql`. `validate-data` writes the resolved copy to `results/runs/<run_id>/duckdb_views.sql`; no database file is created or committed |
| Snapshot lock | `src/nem_forecast/ingestion/snapshot.py` (`build_lock`, `verify_lock_against_local`); CLI `nem-forecast aemo-snapshot-lock` |
| Config | `configs/aemo_source.yaml`: `historical_snapshot.snapshot_id`, `lock_manifest` |

Rules implemented:
- **Endpoint normalisation:** records ending 00:05 … 01:00 → hour labelled
  00:00. The record ending 00:00 → the preceding hour.
- **Selection:** ordinary single records; `INTERVENTION=1` for physical +
  pricing pairs; never combined.
- **Completeness:** all 12 expected endpoints exactly once after selection,
  finite values, VIC1 only, consistent `DISPATCHINTERVAL`, no quarantined
  record. Reason flags: `no_records`, `missing_endpoint`, `duplicate_endpoint`,
  `unexpected_endpoint`, `nonfinite_demand`, `quarantined_record`,
  `non_vic1_region`, `dispatchinterval_mismatch`, `outside_canonical_range`.
- **Aggregation:** arithmetic mean of 12 equal-duration values. Incomplete
  hours keep a null target and stay in the canonical output.
- **No availability buffer** (D-07 belongs to Phase 3).

## Commands and outcomes

Interactive:

```bash
.venv/bin/nem-forecast aemo-snapshot-lock --snapshot DISPATCHREGIONSUM_2022-01_2026-09_retrieved20261014
#   OK: lock configs/data_snapshots/…lock.json | archives 57 | bytes 319479933 | sha256 25ebc30a…6180
.venv/bin/python -m pip install -q -e ".[dev]"     # adds duckdb 1.5.6
```

Code was committed locally as `468c0af` before the run, so the run manifest
records a clean tree. SLURM job **32947283** (`scratch/phase2_run.sbatch`,
COMPLETED 0:0, 1 min 54 s, `commit=468c0af dirty=0`):

| Command | Outcome |
|---|---|
| `pip check` | No broken requirements |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 44 files already formatted |
| `mypy` (strict) | Success: no issues found in 23 source files |
| `pytest --cov=nem_forecast` (offline) | **163 passed**, 4 deselected; coverage 91% |
| `pytest -m integration -v` (bounded) | **3 passed**, including the real 2022-06-15 06:00 intervention hour |
| `nem-forecast validate-config` | OK |
| `nem-forecast build-hourly --run-id phase2_vic1_hourly_20261014_full` | `OK: … hours 41616 \| complete 41616 \| incomplete 0 \| model-ready 41616 \| git dirty False`; 60 s, max RSS 0.99 GB |
| `nem-forecast validate-data --run-id phase2_vic1_hourly_20261014_full` | `OK: 13 checks, failed [] \| max \|target diff\| 3.64e-12 MW`; 20 s |

During development, one test expectation was wrong: an hour with no records
also correctly reports `missing_endpoint`. The test was corrected. All other
failures were lint or typing issues, fixed before the run.

## Reconciliation (run `phase2_vic1_hourly_20261014_full`)

| Item | Pipeline | Independent (stdlib csv + DuckDB) |
|---|---|---|
| Raw records, all regions | 2,514,395 | 2,514,395 |
| Raw VIC1 records | 502,879 | 502,879 |
| Selected primary records | 499,392 (= 495,905 ordinary + 3,487 physical) | 499,392 |
| Unselected records | 3,487 (all pricing-run rows) | 3,487 |
| Quarantined records / intervals | 0 | 0 |
| Hourly records | 41,616 | 41,616 |
| Complete hours | 41,616 | 41,616 (12 rows, 12 unique on-grid endpoints, offsets 5…60 min, 12 finite values) |
| Incomplete or invalid hours | 0 | 0 completeness disagreements |
| Model-ready rows | 41,616 | 41,616 (0 missing from SQL) |
| Intervention-affected hours | 297 | 297 |
| Max \|pipeline − SQL\| target | — | 3.64 × 10⁻¹² MW (tolerance 10⁻⁹) |

All of these expectations were derived from the frozen window and checked, not
hard-coded:
- 41,616 = 1,734 days × 24;
- 499,392 = 41,616 × 12;
- 12 endpoints per hour;
- zero incomplete hours.

All 16 pipeline-derived invariants passed. Every reason count is 0. The 13 independent checks are in the table above.

## Coverage

| Item | Value |
|---|---|
| First canonical hour | 2022-01-01 00:00 +10:00 (2021-12-31 14:00 UTC); first source stamp `2022/01/01 00:05:00` |
| Last canonical hour | 2026-09-30 23:00 +10:00 (2026-09-30 13:00 UTC); last source stamp `2026/10/01 00:00:00` |

## Yearly counts (market-time year; identical in the pipeline and DuckDB)

| Year | Hours | Complete | Incomplete | Intervention-affected | Mean MW | Min MW | Max MW |
|---|---|---|---|---|---|---|---|
| 2022 | 8,760 | 8,760 | 0 | 292 | 4841.4663 | 2032.8517 | 8308.0267 |
| 2023 | 8,760 | 8,760 | 0 | 3 | 4643.5664 | 1487.1883 | 8764.8992 |
| 2024 | 8,784 | 8,784 | 0 | 2 | 4733.0037 | 1540.2750 | 9431.1183 |
| 2025 | 8,760 | 8,760 | 0 | 0 | 4881.1661 | 1266.7567 | 9314.8958 |
| 2026 (to Sep) | 6,552 | 6,552 | 0 | 0 | 5243.8498 | 1430.4708 | 10466.4325 |

## Target summary (41,616 complete hours, MW)

| Statistic | Value |
|---|---|
| mean | 4848.6234 |
| std | 1093.6866 |
| min | 1266.7567 |
| p01 | 2394.3122 |
| p05 | 3170.4306 |
| median | 4729.0463 |
| p95 | 6867.1846 |
| p99 | 7691.6358 |
| max | 10466.4325 |

Intervention-affected hours: 297, of which 10 contain a non-zero physical −
pricing difference (max |diff| 19.97 MW, June 2022). In those hours only the
physical values enter the mean.

## Output hashes (git-ignored; SHA-256)

| Dataset | Year | Rows | SHA-256 |
|---|---|---|---|
| canonical_hourly | 2022 | 8,760 | `26957d16df267cc591d4a4be9540d4c628551cded97e344a3240f77432013f34` |
| canonical_hourly | 2023 | 8,760 | `9b3dd815c19831c6a72ba8f8cdda16abe385c26012abf7cbb14fd00dcd128537` |
| canonical_hourly | 2024 | 8,784 | `4a0ff963e91eacdffdb0fa8de5401fe758bf833245fc639478e64da9c4bdb0ef` |
| canonical_hourly | 2025 | 8,760 | `aac35f55cf6a01bd68a5d54ff14f873b7f2fbab7a1bb3006783d63d0ad74579b` |
| canonical_hourly | 2026 | 6,552 | `293013449e94a8daab476b7f914e3a484e6a233abce2af038eec7d581a3353fe` |
| model_ready | 2022 | 8,760 | `ce4d9aed9ddfaa125415dbf2850af7696e13fe118d320fb763b9d96047ca2f26` |
| model_ready | 2023 | 8,760 | `ccddaad5b8987a9dd38f9ae585a141645a0d77b557cac1ebf77a5cb39134f7d2` |
| model_ready | 2024 | 8,784 | `3eebb0825a70bf695bc8ceeb58ea514c662727c59024fcfeeecb9b4f4104ed96` |
| model_ready | 2025 | 8,760 | `60bc57fb9e6f23534b08138f9cacc47a64f9968284c299b677097b898b1312e7` |
| model_ready | 2026 | 6,552 | `7648db78224689da3398e882874448281b5bddf6649389c27d6b3872fdde9ef6` |
| intervals | 2022 | 108,565 | `bf391045c8c929b03e839d8201c9585307bc5ee053bc7a4888bf770d3b7ae0c4` |
| intervals | 2023 | 105,150 | `9c33fb68bdea057674a408d14558f80ff4706c133e744e8187c342a5635b39d8` |
| intervals | 2024 | 105,420 | `187a7abe6ccae3b7a738b5b1917da977c93ba71662c4174b9e107c2bfae18310` |
| intervals | 2025 | 105,120 | `c84fb62bb0ef72a93e3d9f35aa2de41425e420de30c4ca087c8483aa3e642a92` |
| intervals | 2026 | 78,624 | `5b8917e51ef1cf5df3f2b76efe188964c6c55d6354222fa27f667a3f7a599243` |

Interval partitions follow the canonical hour's year. 2022, 2023 and 2024 hold
3,445, 30 and 12 unselected pricing rows respectively.

Reports and manifests:

| File | SHA-256 |
|---|---|
| `quality_report.json` | `2458b2f7dfd3e94692f699b2f3a2bc0daa43987a09760f768a4a469c9290a5b8` |
| `quality_report.md` | `b19a0cc27c461d38e899f618e815df128a683d1ebffa58df0f78bed3ef58f1bd` |
| `reconciliation.json` | `fe8e084a09cf3a7f4412338b5d098ffacac33d0ea5cd6815d94625a77142487a` |
| `run_manifest.json` | `da3ed96c8904b5b9a7541d51012e2ad12e0ade2b27d93cf128ebba9909ed23da` |
| `independent_reconciliation.json` | `58db8dc3b2c00a4d3e321fb472ae3eb32bd105ce122a6dc518e022eaef17bbc4` |

The run manifest records:
- the snapshot ID and the lock path and hash;
- 57 input hashes;
- the commit `468c0af9bd340b2e5a2dff740ca9591f21e6dd25` with `dirty: false`;
- environment versions and the resolved configuration;
- the target semantics;
- row counts, coverage, quality results and output hashes.

## Warnings and unexpected conditions

- The pipeline emitted **no warnings**. There were no incomplete hours, no
  quarantined records, no non-positive or non-finite VIC1 values, and no
  unparsable timestamps.
- The pipeline and DuckDB targets differ by at most 3.6 × 10⁻¹² MW. This is
  floating-point summation order, well inside the 10⁻⁹ MW tolerance.
- Limitations carried forward (not new):
  - snapshot values are last-changed values (`LASTCHANGED` rewrites up to ~63 h
    in 2022–2024);
  - the intervention flag documentation is applied retrospectively;
  - 2026 covers January to September only.

## Not done (by instruction)

No feature engineering, weather, models, API or dashboards. No
`data-contract-v1` tag. No data, manifests, logs, databases or SLURM files
are committed.

## Claim status

| Claim | Basis |
|---|---|
| 41,616 complete hours, reconciliation figures, hashes | Fresh run (job 32947283), independently re-derived |
| Target semantics and selection rule | Documentation (D5, D10) + owner decisions D-01, D-06 |
| Edge-case behaviour | Hand-calculated offline tests |
| Real-time availability of values | Not established (limitation) |
