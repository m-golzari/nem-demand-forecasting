# Feature registry

No features are implemented yet (Phase 3). A feature may be used only if it is
registered here. The rows below are **planned** and not yet implemented.

Column meanings:

- **Represented timestamp**: the time the value describes.
- **Latest source timestamp**: the latest source interval *end* the value depends on, relative to origin `O` and target-hour start `T`.
- **Available at origin**: whether that latest source timestamp is ≤ `O`.

| Name | Source | Transformation | Represented timestamp | Latest source timestamp | Available at origin | Permitted experiments | Status |
|---|---|---|---|---|---|---|---|
| `lag_24h` | S-01 hourly mean MW | value at `T − 24 h` | `T − 24 h` | `T − 23 h` ≤ `O` for h ≤ 24 | Yes | primary, oracle | planned |
| `lag_48h` | S-01 | value at `T − 48 h` | `T − 48 h` | `T − 47 h` | Yes | primary, oracle | planned |
| `lag_168h` | S-01 | value at `T − 168 h` | `T − 168 h` | `T − 167 h` | Yes | primary, oracle | planned |
| same-hour / day-type rolling means | S-01 | trailing windows ending ≤ `O` | window | ≤ `O` | Yes | primary, oracle | planned |
| target calendar (hour, weekday, month, …) | derived from `T` | deterministic | `T` | none | Yes | primary, oracle | planned; time basis is decision O-06 |
| Victorian public holiday | S-04 | holiday flag for target date | `T` | none (published calendar) | Yes | primary, oracle | planned |
| observed temperature at `T` | S-03 | — | `T` | `T + 1 h` > `O` | **No** | `realised_weather_oracle` only | planned (Phase 5) |

Check for `lag_24h`: at h = 24, `T = O + 23 h`, so the source interval
`[O − 1 h, O)` ends exactly at `O`. Whether that is realistic depends on
publication latency (decision O-05).
