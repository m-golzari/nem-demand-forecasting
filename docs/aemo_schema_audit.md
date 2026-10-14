# AEMO DISPATCHREGIONSUM schema audit (Phase 1)

Sample: VIC1 market dates **2024-04-04 to 2024-04-10 inclusive** (end-exclusive
2024-04-11), i.e. canonical intervals `[2024-04-04 00:00, 2024-04-11 00:00)` NEM
market time. The window includes Victoria's 2024 daylight-saving end
(Sunday 2024-04-07, 03:00 AEDT → 02:00 AEST).

Machine-readable evidence (regenerable, git-ignored):
`data/interim/aemo/dispatchregionsum/audit_vic1_20240404_20240411.json`,
`.../vic1_20240404_20240411.parquet` and `.manifest.json`.

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
| `INTERVENTION` | DISPATCHREGIONSUM: “Manual Intervention flag”, part of the primary key. Sibling tables (e.g. PREDISPATCHREGIONSUM): “sourced from the pricing run (INTERVENTION=0) or the physical run (INTERVENTION=1). In the event there is not intervention in the market, both pricing and physical runs correspond to INTERVENTION=0”. DISPATCHCASESOLUTION refers to “package documentation”, which was not found in the report. | [DOC] D1, D2, D4 |
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

## 5. Source-to-canonical mapping

Canonical hour `H` (start-labelled, owner decision A-01) contains every record
whose represented interval `(SETTLEMENTDATE − 5 min, SETTLEMENTDATE]` lies in
`[H, H + 1 h)`. The canonical hour `2024-04-04 00:00` (+10:00) is formed by
exactly these 12 records (from the audit JSON):

`2024/04/04 00:05:00`, `00:10`, `00:15`, `00:20`, `00:25`, `00:30`, `00:35`,
`00:40`, `00:45`, `00:50`, `00:55`, `2024/04/04 01:00:00`

The record stamped `2024/04/04 00:00:00` belongs to hour `2024-04-03 23:00`.

Why the label is 00:00: the forecast contract defines horizon 1 as
`[00:00, 01:00)`, labelled by its start. AEMO's interval-ending stamps are
preserved in `settlementdate_source`. Only the canonical columns use start
labels: `interval_start_*`, `interval_end_*`, `hour_start_market`, and
`hour_start_utc` (UTC = market − 10 h). This is tested in
`tests/test_time_semantics.py`.

Because `TOTALDEMAND` is an end-of-interval value (§2), the 12 values are point
estimates at 00:05, …, 01:00. Their mean is the hourly target. No time
weighting beyond equal 5-minute spacing applies (all hours complete).

## 6. Duplicates, intervention, and revisions

- **This sample:** no duplicates and no intervention records. There is exactly
  one record per (`SETTLEMENTDATE`, `REGIONID`) [RAW].
- **General rule: not established. Owner review required (O-03).** By official
  definition, intervention intervals carry two rows: the pricing run
  (`INTERVENTION=0`) and the physical run (`INTERVENTION=1`). Which row
  represents the demand target is not stated for DISPATCHREGIONSUM. D5 says
  `TOTALDEMAND` is computed *before* optimisation, which suggests (**[INF]**,
  untested) that both rows carry the same value. April 2024 contains no
  interventions, so this cannot be checked from the approved sample.
  **No rule was invented.** The normaliser keeps all rows and flags
  `q_has_intervention_counterpart` and `q_pk_duplicate`.
- **Revisions:** the archive is a snapshot “at the time the archive was
  created” [DOC D8]. The 2024-08 to 2024-12 archives are listed with a
  2026-05-19 timestamp, i.e. re-issued long after the month. From 2024-08 all
  26 months use the new `PUBLIC_ARCHIVE#…#FILE01#…` naming [RAW D7]. The HTTP
  `Last-Modified` of the April 2024 file (2026-04-16) differs from its listing
  time (2024-05-11). Upstream files can therefore change. Manifests record the
  listing time, HTTP headers, and SHA-256, so a later re-download can be
  compared, never silently substituted.

## 7. Publication latency (availability at origin)

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
- **Recommendation for Phase 3:** exclude source intervals ending in the final
  hour before the origin from the primary features. This is a feature
  availability lag of 1 h: the latest usable record ends at origin − 1 h. It
  covers the observed 25-minute delays. Report a 0-lag sensitivity run
  separately. This changes `lag_24h` for h = 24 (see the feature registry).
  Owner decision O-05.

## 8. Adapter choice (O-04)

Direct NEMWeb MMSDM is the authoritative input:
- the daily `Reports/Archive/DispatchIS_Reports` only covers 2025-10-03 to
  2026-10-12 (rolling), so 2022–2024 history exists only in MMSDM [RAW D7];
- `nemosis` downloads the same MMSDM files and would add a dependency and cache
  without adding provenance. It was not used. It may be added later as an
  optional cross-check.

## 9. Known limitations

- Only one month and one region were inspected in depth. No intervention
  periods were observed.
- The column-kind inference is heuristic (all values parse as numbers or
  dates) and is not an official dtype.
- The Data Model Report version differs from the archive's report version 5.
