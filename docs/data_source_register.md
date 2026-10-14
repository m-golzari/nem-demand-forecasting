# Data source register

No data has been downloaded. Entries are candidates until Phase 1 verifies
them against official documentation and raw responses.

| ID | Source | Use | Table / endpoint | Status | Licence / attribution | Raw location | Notes |
|---|---|---|---|---|---|---|---|
| S-01 | AEMO NEMWeb / MMS Data Model | Target `TOTALDEMAND`, VIC1 | Candidate `DISPATCHREGIONSUM` (5-min) | Not verified; no downloads | *To record in Phase 1* (AEMO copyright permissions) | `data/raw/aemo/` (planned) | Audit timestamps, intervention/run/version fields, revisions |
| S-02 | `nemosis` (third-party) | Optional adapter for S-01 | — | Not evaluated | Package licence to record | Its cache cannot be the only evidence | Decision O-04 |
| S-03 | Open-Meteo historical API | Weather EDA / oracle experiment only (Phase 5) | Melbourne historical | Not used | Terms and attribution to record when used | `data/raw/open_meteo/` (planned) | Never in the primary operational model |
| S-04 | `holidays` Python package (AU, VIC subdivision) | Known public holidays (Phase 3) | — | Not used | MIT (verify) | — | Check against official Victorian holiday list |

Every downloaded artefact will be recorded in a manifest with URL/table, request
parameters, retrieval UTC time, bytes, SHA-256, and path.
