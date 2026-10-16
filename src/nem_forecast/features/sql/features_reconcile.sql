-- Compact, independent reconciliation of a Phase 3 run (paths substituted at runtime).
-- All timestamps are compared as naive UTC.
SET TimeZone = 'UTC';

CREATE OR REPLACE VIEW f AS
SELECT *, filename AS source_file,
       CAST(origin_utc AS TIMESTAMP) AS o, CAST(target_hour_utc AS TIMESTAMP) AS t,
       CAST(cutoff_utc AS TIMESTAMP) AS c
FROM read_parquet('{features_glob}', filename = true, hive_partitioning = false);

CREATE OR REPLACE VIEW a AS
SELECT *, filename AS source_file FROM read_parquet('{audit_glob}', filename = true, hive_partitioning = false);

-- Phase 2 hourly observations with their ACTUAL latest source endpoint (market -> UTC).
CREATE OR REPLACE VIEW h AS
SELECT CAST(hour_start_utc AS TIMESTAMP) AS label,
       is_complete,
       dispatch_totaldemand_hourly_mean_mw AS v,
       strptime(last_source_settlementdate, '%Y/%m/%d %H:%M:%S') - INTERVAL 10 HOUR AS endpoint
FROM read_parquet('{hourly_glob}', hive_partitioning = false);

-- Target-relative lags recomputed with the cutoff applied to source endpoints.
CREATE OR REPLACE VIEW lag_sql AS
SELECT f.availability_profile, f.o, f.horizon, k.k,
       coalesce(h.is_complete AND h.endpoint <= f.c, false) AS available_sql,
       h.v AS value_sql,
       CASE k.k WHEN 24 THEN f.lag_24h WHEN 48 THEN f.lag_48h ELSE f.lag_168h END AS value_pipe,
       CASE k.k WHEN 24 THEN f.lag_24h__available WHEN 48 THEN f.lag_48h__available
                ELSE f.lag_168h__available END AS available_pipe
FROM f
CROSS JOIN (VALUES (24), (48), (168)) AS k(k)
LEFT JOIN h ON h.label = f.t - to_hours(k.k);

-- recent_24h_mean: the 24 labels ending at cutoff - 1 h (origin-relative; horizon 1 rows).
CREATE OR REPLACE VIEW recent_sql AS
SELECT f.availability_profile, f.o,
       count(h.label) AS n,
       coalesce(bool_and(h.is_complete AND h.endpoint <= f.c), false) AS all_ok,
       avg(h.v) AS mean_sql,
       any_value(f.recent_24h_mean) AS mean_pipe,
       any_value(f.recent_24h_mean__available) AS available_pipe
FROM f
LEFT JOIN h ON h.label BETWEEN f.c - INTERVAL 24 HOUR AND f.c - INTERVAL 1 HOUR
WHERE f.horizon = 1
GROUP BY f.availability_profile, f.o;
