# AEMO DISPATCHREGIONSUM schema audit (Phases 1 and 1B)

## Reading guide: original Phase 1 record vs current conclusions

This document has two layers. Both are dated.

- **Phase 1 (seven-day reconnaissance, 2024-04-04 to 2024-04-10)**: §3, §4,
  §6.1 and §7.1 record what was observed in that sample. They are kept
  unchanged as a historical record. That sample contained **no intervention
  records** and only one archive month was inspected. Those limits applied to
  Phase 1 only.
- **Current conclusions (Phase 1B, full snapshot 2022-01 to 2026-09)**: §2,
  §5, §6.2, §7.2, §9 and §10. All 57 monthly archives and all five regions were
  audited, including 3,487 intervention intervals. The intervention selection
  rule is **settled** (owner decision D-06), and no intervention interval was
  quarantined.

Phase 1 sample: VIC1 market dates **2024-04-04 to 2024-04-10 inclusive**
(end-exclusive 2024-04-11), i.e. canonical intervals
`[2024-04-04 00:00, 2024-04-11 00:00)` NEM market time. The window includes
Victoria's 2024 daylight-saving end (Sunday 2024-04-07, 03:00 AEDT →
02:00 AEST).

Machine-readable evidence (regenerable, git-ignored):
- Phase 1: `data/interim/aemo/dispatchregionsum/audit_vic1_20240404_20240411.json`;
- Phase 1B: `data/interim/aemo/dispatchregionsum/historical_audit/<snapshot>/historical_audit.json`;
- the public snapshot lock: `configs/data_snapshots/<snapshot>.lock.json`
  (committed; path-neutral).

Evidence labels: **[DOC]** official AEMO documentation, **[RAW]** observed in
the raw archive, **[INF]** inference, **[NE]** not established.

## 1. Official sources consulted

All were retrieved on 2026-10-14 (UTC times shown).

| # | Title (as published) | URL | Retrieved (UTC) |
|---|---|---|---|
| D1 | Electricity Data Model Report: *Table: DISPATCHREGIONSUM* (pages 1–3) | https://nemweb.com.au/Reports/CURRENT/MMSDataModelReport/Electricity/Electricity%20Data%20Model%20Report_files/Elec22.htm (also `Elec22_1.htm`, `Elec22_2.htm`) | 01:09:24, 01:10:11 |
| D2 | Electricity Data Model Report: *Table: DISPATCHCASESOLUTION* | .../Electricity%20Data%20Model%20Report_files/Elec20.htm | 01:09:24 |
| D3 | Electricity Data Model Report: *Package: DISPATCH* | .../Electricity%20Data%20Model%20Report_files/Elec19.htm | 01:09:23 |
| D4 | Electricity Data Model Report: *Table: PREDISPATCHREGIONSUM* (intervention field wording) | .../Electricity%20Data%20Model%20Report_files/Elec49.htm | 01:09:28 |
| D5 | *Demand Terms in EMMS Data Model*, v17.0, July 2025 (AEMO) | https://www.aemo.com.au/-/media/Files/Electricity/NEM/Security_and_Reliability/Dispatch/Policy_and_Process/Demand-terms-in-EMMS-Data-Model.pdf | 01:10:35 |
| D6 | *Spot Market Operations Timetable*, v1.10, effective 31 July 2025 (AEMO) | https://aemo.com.au/-/media/files/stakeholder_consultation/consultations/nem-consultations/2024/st-pasa-procedures-and-related-documents/spot-market-operations-timetable.pdf?la=en | 01:11:32 |
| D7 | NEMWeb MMSDM archive listings (2024 and 2024-04 `DATA/`) | https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/2024/MMSDM_2024_04/MMSDM_Historical_Data_SQLLoader/DATA/ | 01:08:34; raw copy saved at 01:18:31 |
| D9 | *Guide to Intervention Pricing*, v2.3, May 2025 (AEMO) | https://www.aemo.com.au/-/media/files/electricity/nem/market_notices_and_events/market_event_reports/guide-to-intervention-pricing.pdf?la=en | 01:58:10 |
| D10 | *Intervention Pricing Methodology*, v2.3, effective 3 June 2024 (AEMO) | https://www.aemo.com.au/-/media/files/electricity/nem/security_and_reliability/dispatch/policy_and_process/intervention-pricing-methodology.pdf?la=en | 01:58:20 |
| D8 | MMSDM archive `README.txt` (v4.12 era) | https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/2024/MMSDM_2024_04/MMSDM_Historical_Data_SQLLoader/DOCUMENTATION/README.txt | ~01:09 |

Notes on the sources:
- The canonical D6 location
  (`https://www.aemo.com.au/-/media/files/electricity/nem/security_and_reliability/dispatch/spot-market-operations-timetable.pdf`)
  returned HTTP 403 to automated clients (curl and WebFetch). The AEMO-hosted
  copy used here states version 1.10, effective 31 July 2025.
- The Data Model Report is the *current* version. The archive was written with
  report version 5 of `DISPATCH,REGIONSUM` (§3). Field definitions used here
  apply to fields present in both.
- Third-party material (an R package vignette and blog posts) appeared in
  searches. It was **not** used as evidence.

## 2. Semantics established

| Topic | Finding | Evidence |
|---|---|---|
| Market time | “References to time are references to Australian Eastern Standard Time (AEST)”. The trading day runs “from 0400 hrs to 0400 hrs the following day”. AEST is UTC+10 with no daylight saving. | [DOC] D6 §1.2. [RAW] §4: DST-date check |
| `SETTLEMENTDATE` | DISPATCHREGIONSUM: “Market date and time starting at 04:05”. In the same DISPATCH package, DISPATCHCASESOLUTION gives “Date and time of the dispatch interval (e.g. five minute dispatch interval **ending** 28/09/2000 16:35)”. D5 refers to “trading interval ending 0310 hrs”. | [DOC] D1, D2, D5 |
| Interval semantics | `SETTLEMENTDATE` is the **end** of the 5-minute interval `(end − 5 min, end]`. | [DOC] D1, D2. [RAW] `DISPATCHINTERVAL` matches the interval-ending formula for 2016/2016 records, e.g. `2024/04/01 00:05` ↔ `20240331241`. The month archive spans `04/01 00:05` to `05/01 00:00`. |
| `DISPATCHINTERVAL` | “Dispatch period identifier, from 001 to 288 in format YYYYMMDDPPP”. YYYYMMDD is the **trading day** (04:00–04:00). | [DOC] D1. [RAW] 0 mismatches |
| `TOTALDEMAND` meaning | D1 says “Demand (less loads)”. D5 §2.1.2 says Total Demand is “the forecast demand at the Regional Reference Node (RRN) that is met by local generation from scheduled and semi-scheduled generating units and scheduled BDUs plus interconnector imports, excluding the local demand of scheduled loads … and the allocated interconnector losses, but including the demand met by Wholesale Demand Response”. In Dispatch it is SCADA initial values **plus `DemandForecast` and `AggregateDispatchError`**, and it is “determined before the optimisation process”. D5 Table 2 lists its forecast type as **50% POE**. | [DOC] D1, D5 |
| `TOTALDEMAND` unit | MW. D5 defines demand as “the electrical power requirement (in megawatts, or MW)”, and the Table 4 formula sums MW quantities (InitialMW, MeteredMWFlow, MWLosses). D1 states no unit for this column. | [DOC] D5 §1, Table 4 |
| `RUNNO` | “Dispatch run no; always 1” | [DOC] D1. [RAW] only value 1 in the file |
| `INTERVENTION` | **Resolved (Phase 1B, [DOC] D10 §3.3):** “The base case target run solution is flagged as ‘Intervention=1’ and the intervention pricing run solution is flagged as ‘Intervention=0’.” D10's glossary says the outturn run is the “central dispatch run used to set dispatch targets … Also known as the ‘intervention run’ or ‘base case target run’”. D9 says the What-If/Pricing run sets settlement prices. Earlier evidence: DISPATCHREGIONSUM: “Manual Intervention flag”, part of the primary key. Sibling tables (e.g. PREDISPATCHREGIONSUM): “sourced from the pricing run (INTERVENTION=0) or the physical run (INTERVENTION=1). In the event there is not intervention in the market, both pricing and physical runs correspond to INTERVENTION=0”. DISPATCHCASESOLUTION refers to “package documentation”, which was not found in the report. **Scope note:** D10 is the methodology current at retrieval (v2.3, effective 3 June 2024). It is applied **retrospectively** to archives back to 2022-01. Earlier methodology versions were not retrieved, so the flag meaning for 2022–2024-05 rests on this current statement plus the consistent observed pattern (§10.3). This scope note does not reopen the D-06 selection rule. | [DOC] D1, D2, D4, D10 |
| Primary key | `SETTLEMENTDATE, RUNNO, REGIONID, DISPATCHINTERVAL, INTERVENTION`; `LASTCHANGED` is an index column | [DOC] D1 |
| `LASTCHANGED` | “Last date and time record changed” | [DOC] D1 |
| Update cadence | “DISPATCHREGIONSUM updates every 5 minutes”. Dispatch runs every 5 minutes. Prices are published “within 5 minutes of AEMO running dispatch algorithm”. Regional “MW load and generation” for Day 0 is published at the start of the Day +1 trading day (NER 3.13.4(n)). | [DOC] D1, D6 §2.3–2.4 |
| Archive contents | Monthly archive tables “include all data at the time the archive was created” | [DOC] D8 |

### Material discrepancy recorded (decision D-01)

CLAUDE.md describes the target as AEMO regional `TOTALDEMAND` and treats it as
power in MW. The unit is confirmed. However, D5 shows that `TOTALDEMAND` is
**not a metered average** over the interval. It is NEMDE's pre-dispatch
**forecast of scheduled (“as generated”) demand at the regional reference node
for the end of the interval**. It excludes non-scheduled generation and rooftop
PV, and includes the 5-minute demand forecast adjustment and aggregate dispatch
error.

The hourly mean of the 12 values is still well defined in MW. It must be
described as “hourly mean of dispatch `TOTALDEMAND`”, not as measured
consumption or AEMO “operational demand” (a separate, measured product).
**The target was not changed.** See `docs/decisions.md` D-01.

## 3. Raw archive facts (whole April 2024 file) [RAW]

| Item | Value |
|---|---|
| File | `PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.zip`, 4,571,235 bytes, SHA-256 `27dc9fb1a4e3054349f20876eb04d538e51c47b22c8a9bdd593567b3429a0a81` |
| Member | `PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.CSV`, 17,435,301 bytes |
| Control rows | `C,SETP.WORLD,DVD_DISPATCHREGIONSUM,AEMO,PUBLIC,2024/05/07,00:15:15,…` and `C,"END OF REPORT",43203` (the count is **all lines**, = 1 C + 1 I + 43,200 D + 1 C) |
| Header | `I,DISPATCH,REGIONSUM,5,…`: report version **5**, 114 columns |
| Column kinds | 67 entirely empty (mostly fields “Not used since Dec 2003”); 35 decimal; 9 integer; 2 datetime strings (`SETTLEMENTDATE`, `LASTCHANGED`, format `YYYY/MM/DD HH:MM:SS`, quoted, naive); 1 string (`REGIONID`) |
| Data rows | 43,200 = 30 days × 288 intervals × 5 regions (NSW1, QLD1, SA1, TAS1, VIC1: 8,640 each) |
| `SETTLEMENTDATE` range | 2024-04-01 00:05 to 2024-05-01 00:00 (market time) |
| `INTERVENTION` | 0 for all 43,200 rows: **no intervention records in any region this month** |
| `RUNNO` | 1 only |

## 4. VIC1 seven-day window [RAW]

| Check | Result |
|---|---|
| Rows (VIC1) | 2,016 = 7 × 288 (all regions in window: 10,080 = 5 × 2,016) |
| Missing / unexpected intervals | 0 / 0 |
| Spacing | 2,015 gaps, all exactly 5 minutes |
| Intervals per market date | 288 on every date, **including the DST date 2024-04-07** |
| Primary-key duplicates; duplicate (`SETTLEMENTDATE`, `REGIONID`) | 0; 0 |
| `INTERVENTION` values | {0: 2016} |
| `DISPATCHINTERVAL` mismatches vs interval-ending formula | 0 |
| `TOTALDEMAND` nulls / non-finite / ≤ 0 | 0 / 0 / 0 |
| `TOTALDEMAND` MW | min 3070.20, p1 3166.86, median 4398.59, mean 4475.15, p99 6013.38, max 6202.99 |
| Largest 5-minute changes | 345.68 MW (04-04 10:40), 299.38 (04-09 10:40), 208.20 (04-04 08:50), 207.74 (04-10 23:10), 195.31 (04-05 09:20). Flagged for awareness, not modified |
| Canonical hours | 168 hours, each with exactly 12 records |

### DST evidence for fixed market time

On 2024-04-07, Victorian civil time repeated 02:00–03:00. The source shows
288 uniformly spaced records with no repeated or missing hour. Records
`2024/04/07 02:00` and `03:00` convert to 2024-04-06 16:00Z and 17:00Z, one hour
apart. If `SETTLEMENTDATE` were Melbourne civil time, this date would show 300
intervals or a duplicated hour. **[RAW + DOC] Source timestamps are fixed
UTC+10.**

## 5. Source-to-canonical mapping (explicit endpoint normalisation)

AEMO's source support for an hour is **left-open, right-closed**. The hour
`(00:00, 01:00]` is carried by the 12 interval-ending records stamped 00:05,
00:10, …, 01:00. The canonical representation (owner decision A-01) is the
**half-open** window `[00:00, 01:00)`, labelled by its start, **00:00**.

This is an explicit endpoint normalisation of the same hour, not a time shift.
The canonical hour `2024-04-04 00:00` (+10:00) is formed by exactly these 12
records (from the audit JSON):

`2024/04/04 00:05:00`, `00:10`, `00:15`, `00:20`, `00:25`, `00:30`, `00:35`,
`00:40`, `00:45`, `00:50`, `00:55`, `2024/04/04 01:00:00`

The record stamped `2024/04/04 00:00:00` closes the previous hour
`(23:00, 00:00]`, canonically `[2024-04-03 23:00, 2024-04-04 00:00)`.

Original stamps are preserved in `settlementdate_source`. Canonical columns
carry the normalised representation: `interval_start_*`, `interval_end_*`,
`hour_start_market`, and `hour_start_utc` (UTC = market − 10 h). The boundary
mapping is tested in `tests/test_availability.py::test_endpoint_normalisation_boundaries`
and `tests/test_time_semantics.py`.

Because `TOTALDEMAND` is an estimate for the end of each interval (§2), the 12
values are end-of-interval estimates. The hourly target is their mean: the
hourly mean of AEMO dispatch `TOTALDEMAND` estimates.

## 6. Duplicates, intervention, and revisions

### 6.1 Phase 1 seven-day finding (historical record, 2024-04-04 to 2024-04-10)

- The sample had no duplicates and no intervention records. There was exactly
  one record per (`SETTLEMENTDATE`, `REGIONID`) [RAW].
- At that time, which run should represent the target was an open owner
  question. The sample could not test whether physical and pricing values
  differ.

### 6.2 Current conclusion (Phase 1B, full snapshot)

- **Selection rule (settled, owner decision D-06):** one ordinary record →
  retained. Exactly one physical (`INTERVENTION=1`, base case target run) and
  one pricing (`INTERVENTION=0`) record → the physical record is selected and
  the difference recorded. Any other combination → quarantined. Both rows are
  preserved upstream; they are never combined.
- **Observed:** every intervention interval in 2022-01 to 2026-09 is exactly
  one physical plus one pricing record, so 0 were quarantined. The two rows'
  `TOTALDEMAND` is identical in 3,448 of 3,487 VIC1 intervals. The other 39
  differ by up to 19.97 MW (§10.3). The Phase 1 inference that both rows would
  carry the same value is therefore **only mostly true**.
- **Revisions:** the archive is a snapshot “at the time the archive was
  created” [DOC D8]. The 2024-08 to 2024-12 archives are listed with a
  2026-05-19 timestamp, i.e. re-issued long after the month. From 2024-08 all
  26 months use the new `PUBLIC_ARCHIVE#…#FILE01#…` naming [RAW D7]. The HTTP
  `Last-Modified` of the April 2024 file (2026-04-16) differs from its listing
  time (2024-05-11). Upstream files can therefore change. Manifests record the
  listing time, HTTP headers, and SHA-256, so a later re-download can be
  compared, never silently substituted.

## 7. Publication latency (availability at origin)

### 7.1 Phase 1 seven-day finding (historical record)

- `LASTCHANGED − SETTLEMENTDATE` (VIC1, window): median −4.95 min, meaning
  records are typically last changed about 3 s after the interval **start**.
  However, 7 records (2024-04-08 12:40–13:10) changed **up to 25 minutes after
  their interval end**. They are consecutive and written together at
  13:05–13:10, consistent with a delayed or re-run publication [RAW].
- The timetable requires publication within 5 minutes of dispatch (prices) and
  next-day publication of regional MW load [DOC D6].
- **Historical availability at the origin is NOT ESTABLISHED.** `LASTCHANGED`
  is a last-change time, not a first-publication time, and archive timestamps
  postdate the data.
- Phase 1 recommended a 1 h feature availability lag with a 0-lag
  sensitivity run.

### 7.2 Current position

- **Decided (D-07):** primary operational features may use demand intervals
  ending ≤ origin − 1 h. This is a modelling assumption, not a measured
  publication delay. A labelled zero-lag sensitivity profile is kept. The
  buffer applies in Phase 3 feature construction, **not** in Phase 2 target
  aggregation.
- Historical availability remains **not established**. Phase 1B found
  `LASTCHANGED` rewrites up to ~63 h after the interval (§10.4).

## 8. Adapter choice (O-04)

Direct NEMWeb MMSDM is the authoritative input:
- the daily `Reports/Archive/DispatchIS_Reports` only covers 2025-10-03 to
  2026-10-12 (rolling), so 2022–2024 history exists only in MMSDM [RAW D7];
- `nemosis` downloads the same MMSDM files and would add a dependency and cache
  without adding provenance. It was not used. It may be added later as an
  optional cross-check.

## 9. Known limitations (current)

- **Snapshot values:** the snapshot holds last-changed values only. Some
  2022–2024 records were rewritten up to ~63 h later, so real-time values and
  originally published files are not established (§10.4).
- **Flag documentation scope:** the intervention flag documentation (D10,
  effective 2024) is applied retrospectively to 2022–2024 archives (§2 scope
  note).
- **Data model versions:** the current Data Model Report was used, while the
  archives span report versions 5, 8 and 9 (§10.2). Field definitions are
  relied on only for columns present throughout.
- **Column kinds:** the column-kind inference in the Phase 1 audit is
  heuristic, not an official dtype.
- **Depth of audit:** all five regions were audited for coverage and
  intervention patterns. Only VIC1 was examined in depth for target purposes.

## 10. Phase 1B: historical snapshot audit (2022-01 to 2026-09)

**Snapshot.** `DISPATCHREGIONSUM_2022-01_2026-09_retrieved20261014`: 57
official monthly archives, 319,479,933 bytes, retrieved 2026-10-14 between
02:06:06Z and 02:06:53Z (one April 2024 file was retrieved in Phase 1 at
01:18:31Z). This is a 2026 snapshot of historical records, **not** a
reconstruction of files as originally published. Evidence (git-ignored):
`data/interim/aemo/dispatchregionsum/historical_audit/<snapshot>/historical_audit.json`
plus `intervention_rows.parquet` and `quarantined_rows.parquet`.

### 10.1 Files, counts and coverage [RAW]

| Item | Result |
|---|---|
| Archives | 57 of 57 months; one file per month. 31 use the `PUBLIC_DVD_…` name, 26 (from 2024-08) the `PUBLIC_ARCHIVE#…#FILE01#…` name |
| Rows | 2,514,395 = 5 regions × 499,392 primary intervals + 17,435 pricing-run rows |
| `SETTLEMENTDATE` support | 2022-01-01 00:05 to 2026-10-01 00:00 (+10:00), i.e. through the frozen end month 2026-09 |
| Rows outside their archive month's support | 0 in every file. Each archive holds interval ends `(1st 00:00, next 1st 00:00]` |
| Coverage, every region | 499,392 of 499,392 expected intervals; 0 missing, 0 unexpected; all 499,391 gaps are 5 minutes; every market day has 288 primary records |
| `END OF REPORT` count | Equals the total line count in all 57 files |
| PK duplicates (within and across files) | 0 |
| `RUNNO` | 1 only |
| `DISPATCHINTERVAL` vs interval-ending formula | 0 mismatches |
| `TOTALDEMAND` null / non-finite | 0 / 0 |
| `TOTALDEMAND` ≤ 0 | **VIC1 0.** SA1 1,201 records (min −311.1 MW, 2023-09 to 2026-09); NSW1/QLD1/TAS1 0. Flagged, not modified; outside the VIC1 target |
| VIC1 primary `TOTALDEMAND` (MW) | min 1224.77, p0.1 1724.93, median 4727.99, mean 4848.62, p99.9 8565.40, max 10783.74 |

### 10.2 Schema versions and changes [RAW]

| From → to | Report version | Change |
|---|---|---|
| 2022-01 … 2024-04 | 5 | 114 columns |
| 2024-04 → 2024-05 | 5 → 8 | +11 columns: `RAISE1SECLOCALDISPATCH`, `LOWER1SECLOCALDISPATCH`, `RAISE1SECACTUALAVAILABILITY`, `LOWER1SECACTUALAVAILABILITY`, `SS_SOLAR_AVAILABILITY`, `SS_WIND_AVAILABILITY`, `BDU_ENERGY_STORAGE`, `BDU_MIN_AVAIL`, `BDU_MAX_AVAIL`, `BDU_CLEAREDMW_GEN`, `BDU_CLEAREDMW_LOAD` (125 columns) |
| 2024-07 → 2024-08 | 8 → 8 | Column **order** changed (same set). Coincides with the archive file-name change. The reader is name-based, so this has no effect |
| 2025-03 → 2025-04 | 8 → 8 | +2 columns: `BDU_INITIAL_ENERGY_STORAGE`, `DECGEN_INITIAL_ENERGY_STORAGE` (127 columns) |
| 2025-05 → 2025-06 | 8 → 9 | Version bump only; no column change |

No column was removed. All required columns (`SETTLEMENTDATE`, `RUNNO`,
`REGIONID`, `DISPATCHINTERVAL`, `INTERVENTION`, `TOTALDEMAND`, `LASTCHANGED`)
are present in every file. **Schema supported.**

### 10.3 Intervention findings [RAW + DOC]

- **Semantics (confirmed [DOC] D10 §3.3):** `INTERVENTION=1` = physical (base
  case target/outturn) run; `INTERVENTION=0` = pricing (what-if) run. D10 v2.3
  (effective 2024-06-03) is applied retrospectively to the 2022–2024
  intervention episodes. The observed structure (exactly one record of each
  flag per intervention interval, in every region) is consistent with it
  throughout.
- **Pattern:** every (interval, region) has either one record with
  `INTERVENTION=0` (2,479,525) or exactly one physical plus one pricing record
  (17,435 = 3,487 intervals × 5 regions). There are **0 quarantined rows** and
  no other combinations.
- **Scope:** the same 3,487 intervals carry intervention pairs in all five
  regions. This is consistent with D9 (“intervention pricing run are applied to
  all regions”).
- **VIC1 episodes** (12; interval-ending stamps, +10:00):

| First end | Last end | Intervals |
|---|---|---|
| 2022-02-01 17:05 | 2022-02-01 21:30 | 54 |
| 2022-06-13 06:30 | 2022-06-13 08:40 | 27 |
| 2022-06-13 16:05 | 2022-06-13 23:30 | 90 |
| 2022-06-14 04:55 | 2022-06-14 10:00 | 62 |
| 2022-06-14 12:20 | 2022-06-23 04:00 | 2,493 |
| 2022-07-05 08:25 | 2022-07-05 15:30 | 86 |
| 2022-07-05 17:05 | 2022-07-05 18:00 | 12 |
| 2022-11-15 07:05 | 2022-11-15 15:00 | 96 |
| 2022-11-17 07:50 | 2022-11-17 17:00 | 111 |
| 2022-11-18 08:35 | 2022-11-19 19:00 | 414 |
| 2023-02-03 17:05 | 2023-02-03 19:30 | 30 |
| 2024-11-27 15:50 | 2024-11-27 16:45 | 12 |

- **Physical − pricing `TOTALDEMAND`, VIC1:**
  - Identical in 3,448 of 3,487 intervals.
  - The other 39 differ (33 by more than 1 MW, 10 by more than 10 MW, none
    by more than 50 MW; maximum |diff| 19.97 MW, mean −0.07 MW). All 39 fall
    between 2022-06-14 14:10 and 2022-06-16 06:20.
  - TAS1 shows the mirror image: the VIC1 + TAS1 differences sum to ≤ 0.4 MW.
    **[INF]** This is consistent with different Basslink flow/loss terms
    between runs, since D10 says the pricing run uses “What-if” initial
    interconnector loadings. This is not verified.
  - NSW1, QLD1 and SA1 differences are exactly 0.
- **VIC1 hours affected:** 297 canonical hours contain at least one
  intervention interval. Under D-06 each such interval contributes only its
  physical record.

### 10.4 `LASTCHANGED` (descriptive only; not availability evidence) [RAW]

- The median `LASTCHANGED − SETTLEMENTDATE` is about −4.9 min in every year,
  i.e. records are typically last changed about 3 s after the interval start.
- 3,527 VIC1 records changed after their interval end. Records changed more
  than 60 minutes late cluster in:
  - 2022-06: 693 records, up to 46 h late;
  - 2022-11: 418 records, up to 51 h;
  - smaller clusters in 2022–2024, the largest 63.5 h (2023-03).
- In 2025–2026 no VIC1 record changed more than 3 s after its interval end.
- **Implication:** the snapshot holds only last-changed values. Some
  historical values were rewritten after the fact, so a backtest on this
  snapshot may use values that differ from what was visible in real time.
  This is **not established** either way and is recorded as a limitation.
  D-07's 1 h buffer does not cover multi-hour rewrites.

### 10.5 Open anomalies (reviewed by the owner with Phase 1B approval; none blocks Phase 2)

1. **SA1 non-positive `TOTALDEMAND`** (1,201 records). Not VIC1; flagged only.
   **[INF]** Plausibly genuine low scheduled demand with high rooftop PV; not
   verified.
2. **Late rewrites (`LASTCHANGED` up to ~63 h after the interval):** whether
   values changed, and what was visible in real time, cannot be determined
   from a snapshot.
3. **39 VIC1 intervals with physical ≠ pricing** (≤ 20 MW, June 2022): handled
   by D-06 (physical selected). The cause is inferred, not verified.
4. **The 2022-06-14 → 2022-06-23 episode** (2,493 VIC1 intervals) is the
   largest intervention period. Its external context is not documented here
   and is **not established** from the sources consulted.
