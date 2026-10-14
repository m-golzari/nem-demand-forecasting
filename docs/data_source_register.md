# Data source register

Phase 1B retrieved the owner-approved historical snapshot of AEMO
`DISPATCHREGIONSUM`: 57 monthly archives, 2022-01 to 2026-09 (frozen),
retrieved 2026-10-14. It is a **2026 snapshot of historical records**, not a
reconstruction of files as originally published. Downloaded and transformed
data stay in git-ignored `data/` directories and are never committed.

**S-01 target definition.** The hourly mean of AEMO dispatch `TOTALDEMAND`
estimates for VIC1. It is NEMDE's end-of-interval forecast of demand at the
regional reference node met by scheduled and semi-scheduled generation,
scheduled BDUs and imports. It **excludes** non-scheduled generation, rooftop
PV, scheduled loads and allocated interconnector losses.

It is **not** measured consumption, metered load, total underlying demand,
observed average demand, or AEMO operational demand. Source: AEMO *Demand
Terms in EMMS Data Model* v17.0 §2.1.2.

| ID | Source | Use | Table / endpoint | Status | Licence / attribution | Raw location | Notes |
|---|---|---|---|---|---|---|---|
| S-01 | AEMO NEMWeb MMSDM monthly archive | Target: hourly mean of dispatch `TOTALDEMAND` estimates, VIC1 | `DISPATCHREGIONSUM` (5-min); physical run (`INTERVENTION=1`) selected in intervention intervals (D-06) | **Verified (Phase 1)**; historical snapshot 2022-01..2026-09 retrieved 2026-10-14 (Phase 1B) | AEMO copyright permissions (“may be used in accordance with the copyright permissions on AEMO’s website”); data not redistributed in git | `data/raw/aemo/mmsdm/DISPATCHREGIONSUM/` | See `docs/aemo_schema_audit.md`; intervention rule open (O-03) |
| S-02 | `nemosis` (third-party) | Optional cross-check for S-01 | — | Not used (O-04 resolved) | — | — | Reads the same MMSDM files |
| S-03 | Open-Meteo historical API | Weather EDA / oracle experiment only (Phase 5) | Melbourne historical | Not used | Terms and attribution to record when used | `data/raw/open_meteo/` (planned) | Never in the primary operational model |
| S-04 | `holidays` Python package (AU, VIC subdivision) | Known public holidays (Phase 3) | — | Not used | MIT (verify) | — | Check against official Victorian holiday list |

Every downloaded artefact will be recorded in a manifest with URL/table, request
parameters, retrieval UTC time, bytes, SHA-256, and path.
