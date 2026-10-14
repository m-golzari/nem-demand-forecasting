# Data source register

Phase 1 downloaded one official archive (April 2024 DISPATCHREGIONSUM) for the
seven-day sample. No multi-year download has been made (owner gate).

| ID | Source | Use | Table / endpoint | Status | Licence / attribution | Raw location | Notes |
|---|---|---|---|---|---|---|---|
| S-01 | AEMO NEMWeb MMSDM monthly archive | Target `TOTALDEMAND`, VIC1 | `DISPATCHREGIONSUM` (5-min), report version 5 | **Verified (Phase 1)**; 7-day sample only (April 2024 archive) | AEMO copyright permissions (“may be used in accordance with the copyright permissions on AEMO’s website”); data not redistributed in git | `data/raw/aemo/mmsdm/DISPATCHREGIONSUM/` | See `docs/aemo_schema_audit.md`; intervention rule open (O-03) |
| S-02 | `nemosis` (third-party) | Optional cross-check for S-01 | — | Not used (O-04 resolved) | — | — | Reads the same MMSDM files |
| S-03 | Open-Meteo historical API | Weather EDA / oracle experiment only (Phase 5) | Melbourne historical | Not used | Terms and attribution to record when used | `data/raw/open_meteo/` (planned) | Never in the primary operational model |
| S-04 | `holidays` Python package (AU, VIC subdivision) | Known public holidays (Phase 3) | — | Not used | MIT (verify) | — | Check against official Victorian holiday list |

Every downloaded artefact will be recorded in a manifest with URL/table, request
parameters, retrieval UTC time, bytes, SHA-256, and path.
