# Phase 1 report: seven-day AEMO reconnaissance

Date: 2026-10-14 (system date checked) · Status: **Phase 1 gate passed, with
two owner decisions open.** O-03 (intervention-row rule) must be settled
before Phase 2. O-05 (availability lag) must be settled before Phase 3.
**The multi-year download has NOT been started** (owner gate).

Detailed evidence: `docs/aemo_schema_audit.md`.

## Acceptance criteria (IMPLEMENTATION_PLAN.md, Phase 1)

| Criterion | Status |
|---|---|
| Provenance (URL, retrieval UTC, bytes, SHA-256, path) | Met: manifest + saved listing |
| Timezones | Met: AEST fixed UTC+10 [DOC D6], DST-date check [RAW] |
| Counts | Met: 43,200 → 10,080 → 2,016 reconciled |
| Duplicate policy | Partly met: no duplicates or intervention rows in the sample; the intervention rule is **open for owner review** (O-03), and all rows are preserved |
| VIC1 filter | Met: filtered, asserted, reconciled |
| Units | Met: MW [DOC D5] |
| No critical unexplained shift/schema issue | Met: interval-ending semantics verified three ways; 0 `DISPATCHINTERVAL` mismatches |

## Window and download unit

- **Window:** the preferred 2024-04-04 → 2024-04-11 was kept, as end-exclusive
  market dates (7 days, 2,016 intervals). It spans the Victorian DST end on
  2024-04-07.
- **Download unit:** the only official source covering April 2024 is the
  **monthly** MMSDM archive. It holds all 5 regions for 30 days (4.57 MB zip).
  The daily `DispatchIS_Reports` archive covers only 2025-10-03 to 2026-10-12.
  The archive was kept unchanged as required. Only the 7-day VIC1 window was
  audited in depth and normalised. File-level counts cover the whole archive
  and are used for reconciliation.

## Files created or changed

| Kind | Files |
|---|---|
| New source | `src/nem_forecast/provenance.py`, `ingestion/nemweb.py` (listing resolution, dry-run plan, guarded/hashed/no-overwrite download, retries), `ingestion/aemo_csv.py` (AEMO C/I/D reader, strings preserved), `ingestion/aemo_normalise.py` (typed canonical frame, timestamps, flags, reconciliation), `validation/aemo_audit.py` |
| Changed source | `contract.py` (interval-ending parsing, canonical hour mapping, window bounds), `config.py` (`AemoSourceConfig`), `cli.py` (`aemo-download [--dry-run]`, `aemo-audit`, `aemo-normalise`) |
| Config | `configs/aemo_source.yaml` (new), `pyproject.toml` (httpx, pandas, pyarrow, pandas-stubs; test lint exemptions) |
| Tests | `tests/aemo_fixtures.py` (synthetic only), `test_time_semantics.py`, `test_aemo_csv.py`, `test_aemo_normalise_audit.py`, `test_nemweb.py`, `test_cli_aemo.py`, `test_network_aemo.py` (`@network`) |
| Docs | `docs/aemo_schema_audit.md` (new), this report, `phase_1_volume_estimate.json` (listing metadata only); updated `decisions.md`, `forecast_specification.md`, `data_source_register.md`, `feature_registry.md` |
| Data (git-ignored, local) | `data/raw/aemo/mmsdm/DISPATCHREGIONSUM/2024/PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.zip` (+ manifest, + saved listing; read-only); `data/interim/aemo/dispatchregionsum/{vic1_20240404_20240411.parquet, .manifest.json, audit_vic1_20240404_20240411.json}` |

## Commands and outcomes

Run interactively, in order:

```bash
.venv/bin/python -m pip install -q -e ".[dev]"     # httpx 0.28.1, pandas 2.3.3, pyarrow 21.0.0
.venv/bin/nem-forecast aemo-download --dry-run      # 1 listing + 1 data request; 4,571,235 bytes; destination shown; exit 0
.venv/bin/nem-forecast aemo-download --dry-run --start 2024-01-01 --end 2024-03-01
                                                    # ERROR: DownloadRefusedError ... 60 days exceeds 7 days; exit 1
.venv/bin/nem-forecast aemo-download                # downloaded; HTTP 200; sha256 27dc9fb1…a0a81; manifest written
chmod a-w data/raw/aemo/mmsdm/DISPATCHREGIONSUM/2024/*.zip  # (and manifests/listing) raw made read-only
.venv/bin/nem-forecast aemo-audit                   # VIC1 rows 2016 | missing 0 | pk_duplicates 0 | intervention {'0': 2016}
.venv/bin/nem-forecast aemo-normalise               # 2016 VIC1 rows | raw rows 43200 | in window 10080
```

Development iterations fixed one wrong test expectation (gap count 286 → 285
for 287 observed intervals) and lint configuration. Naive-datetime (DTZ) checks
are exempted in tests only, because the fixtures intentionally mimic naive AEMO
strings.

Final gate: SLURM job **32940040** (`sbatch scratch/phase1_checks.sbatch`,
COMPLETED 0:0, 23 s):

| Command | Outcome |
|---|---|
| `pip check` | No broken requirements, exit 0 |
| `ruff check src tests` | All checks passed, exit 0 |
| `ruff format --check src tests` | 31 files already formatted, exit 0 |
| `mypy` (strict) | Success: no issues found in 17 source files, exit 0 |
| `pytest --cov=nem_forecast` (offline) | **106 passed, 1 deselected**, coverage 93%, exit 0 |
| `nem-forecast validate-config` | OK, exit 0 |
| Smoke: `aemo-audit --force`, `aemo-normalise --force` on the real raw archive, plus assertions | raw SHA-256 re-verified; 2016 rows; 168 hours × 12 records; reconciliation 43200/10080; no quality flags; first hour = records 00:05…01:00; hourly mean 3138.59–6134.91 MW; `SMOKE OK`, exit 0 |
| `pytest -m network` | 1 passed (live listing resolves the April 2024 archive), exit 0 |
| `aemo-download --dry-run` (after download) | `already_present: true`, exit 0 |

## Row-count reconciliation

| Stage | Rows |
|---|---|
| Raw `D` rows in archive (30 days × 288 × 5 regions) | 43,200 |
| `END OF REPORT` declared (all lines incl. 3 control rows) | 43,203 |
| Rows with unparsable `SETTLEMENTDATE` | 0 |
| Rows in window, all regions (5 × 2,016) | 10,080 |
| VIC1 rows in window | 2,016 |
| Rows written to interim Parquet | 2,016 (0 dropped, 0 deduplicated) |
| Canonical hours (12 records each) | 168 |

## Provenance

- **Archive:** `https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/2024/MMSDM_2024_04/MMSDM_Historical_Data_SQLLoader/DATA/PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.zip`
- **Retrieval and size:** retrieved 2026-10-14T01:18:31Z with HTTP 200; 4,571,235 bytes, matching both the listing and `Content-Length`.
- **SHA-256:** `27dc9fb1a4e3054349f20876eb04d538e51c47b22c8a9bdd593567b3429a0a81`
- **Timestamps:** listing time "Saturday, May 11, 2024 10:03 PM"; `Last-Modified: Thu, 16 Apr 2026 08:57:11 GMT`.
- **Saved listing:** SHA-256 `037776ea…bf9d`, 44,022 bytes.
- **Official documents:** URLs, titles and retrieval times are in `docs/aemo_schema_audit.md` §1.

## Estimated multi-year volume (owner gate)

From live listings retrieved 2026-10-14T01:21:39Z (metadata only, saved in
`phase_1_volume_estimate.json`). History 2022-01 → 2026-09 (latest published
month) needs **57 monthly files, one per month, 319.5 MB compressed**. Each is
4.5–6.6 MB and holds all five regions. That is about 1.2 GB uncompressed CSV,
inferred from April's 3.8× ratio. It takes 57 data requests plus 57 listing
requests.

Two caveats:
- File naming changes at 2024-08 (`PUBLIC_ARCHIVE#…#FILE01#…`); this is handled
  and tested.
- The 2024-08 to 2024-12 archives were re-issued on 2026-05-19.

## Owner decisions needed

1. **O-03, intervention rows (before Phase 2):** in intervention intervals,
   should the target use the pricing run (`INTERVENTION=0`), the physical run
   (`INTERVENTION=1`), or both, after checking whether `TOTALDEMAND` is
   identical? The sample has none, so this cannot be tested without approval
   to fetch a month that contains interventions. The multi-year download would
   provide that evidence.
2. **O-05, availability lag (Phase 3):** recommended 1 h, with a 0-lag
   sensitivity run.
3. **D-01, awareness:** `TOTALDEMAND` is a dispatch end-of-interval forecast of
   scheduled demand, not metered demand. The target is unchanged unless you
   decide otherwise.
4. **Multi-year download approval:** 57 files, about 320 MB.

## Risks

- Upstream archives can be re-issued. Manifests make changes detectable, but
  automatic re-verification of upstream files is not implemented (Phase 8).
- Only one month of one region was examined in depth.
- `_load_window_table` handles windows within one archive month only. This is
  deliberate for Phase 1; Phase 2 must extend it.

## Claim status

| Claim | Basis |
|---|---|
| Market time AEST (UTC+10, fixed) | Verified from documentation (D6) + raw data (DST date) |
| `SETTLEMENTDATE` is interval-ending | Verified from documentation (D1, D2) + raw data (`DISPATCHINTERVAL`, month bounds) |
| `TOTALDEMAND` unit MW and meaning (D-01) | Verified from documentation (D5) |
| 2016/2016 intervals, no duplicates, no interventions, no nulls in sample | Raw data, fresh run (job 32940040) |
| Both intervention rows carry identical `TOTALDEMAND` | Hypothesis (not tested) |
| Historical publication latency | **Not established** |
| Multi-year volume ≈ 319.5 MB / 57 files | Fresh listing metadata; uncompressed size is inference |
| All checks pass | Fresh run (job 32940040) |
