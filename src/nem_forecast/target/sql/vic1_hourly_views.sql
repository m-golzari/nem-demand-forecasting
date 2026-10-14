-- Reproducible DuckDB views over one Phase 2 run (paths are substituted at runtime).
-- Usage: nem-forecast validate-data --run-id <run_id>  (writes the resolved SQL into the run dir)
SET TimeZone = 'UTC';

CREATE OR REPLACE VIEW vic1_intervals AS
SELECT * FROM read_parquet('{intervals_glob}', hive_partitioning = false);

CREATE OR REPLACE VIEW vic1_canonical_hourly AS
SELECT *, CAST(hour_start_utc AS TIMESTAMP) + INTERVAL 10 HOUR AS hour_market_naive
FROM read_parquet('{canonical_glob}', hive_partitioning = false);

CREATE OR REPLACE VIEW vic1_model_ready AS
SELECT *, CAST(hour_start_utc AS TIMESTAMP) + INTERVAL 10 HOUR AS hour_market_naive
FROM read_parquet('{model_ready_glob}', hive_partitioning = false);

-- Example: yearly counts and target summary of the model-ready target
-- SELECT year, count(*) AS hours, avg(dispatch_totaldemand_hourly_mean_mw) AS mean_mw
-- FROM vic1_model_ready GROUP BY year ORDER BY year;
