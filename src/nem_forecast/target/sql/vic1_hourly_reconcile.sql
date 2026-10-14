-- Independent reconciliation. Input table raw_vic1_input(settlementdate, runno,
-- intervention, totaldemand) is loaded from the raw archives with the stdlib csv
-- reader, NOT the pipeline parser. Selection, hour mapping and means are re-derived here.
CREATE OR REPLACE VIEW raw_typed AS
SELECT
    settlementdate,
    strptime(settlementdate, '%Y/%m/%d %H:%M:%S') AS end_market,
    CAST(runno AS INTEGER) AS runno,
    CAST(intervention AS INTEGER) AS intervention,
    TRY_CAST(NULLIF(totaldemand, '') AS DOUBLE) AS demand
FROM raw_vic1_input;

CREATE OR REPLACE VIEW raw_groups AS
SELECT
    settlementdate,
    count(*) AS n,
    sum(CASE WHEN intervention = 1 THEN 1 ELSE 0 END) AS n_physical,
    sum(CASE WHEN intervention = 0 THEN 1 ELSE 0 END) AS n_pricing,
    bool_and(runno = 1) AS runno_ok
FROM raw_typed
GROUP BY settlementdate;

CREATE OR REPLACE VIEW raw_selected AS
SELECT t.*
FROM raw_typed t
JOIN raw_groups g USING (settlementdate)
WHERE g.runno_ok
  AND ((g.n = 1 AND g.n_pricing = 1 AND t.intervention = 0)
       OR (g.n = 2 AND g.n_physical = 1 AND g.n_pricing = 1 AND t.intervention = 1));

CREATE OR REPLACE VIEW sql_hourly AS
SELECT
    date_trunc('hour', end_market - INTERVAL 5 MINUTE) AS hour_market_naive,
    count(*) AS n_selected,
    count(DISTINCT end_market) AS n_unique_endpoints,
    count(demand) AS n_valid,
    bool_and(demand IS NOT NULL AND isfinite(demand)) AS all_finite,
    min(end_market - date_trunc('hour', end_market - INTERVAL 5 MINUTE)) AS min_offset,
    max(end_market - date_trunc('hour', end_market - INTERVAL 5 MINUTE)) AS max_offset,
    bool_and(minute(end_market) % 5 = 0 AND second(end_market) = 0) AS on_grid,
    avg(demand) AS mean_mw
FROM raw_selected
GROUP BY 1;

CREATE OR REPLACE VIEW sql_hourly_complete AS
SELECT *
FROM sql_hourly
WHERE n_selected = 12 AND n_unique_endpoints = 12 AND n_valid = 12 AND all_finite AND on_grid
  AND min_offset = INTERVAL 5 MINUTE AND max_offset = INTERVAL 60 MINUTE;

CREATE OR REPLACE VIEW raw_quarantined_intervals AS
SELECT *
FROM raw_groups
WHERE NOT (runno_ok AND ((n = 1 AND n_pricing = 1) OR (n = 2 AND n_physical = 1 AND n_pricing = 1)));

CREATE OR REPLACE VIEW sql_intervention_hours AS
SELECT DISTINCT date_trunc('hour', end_market - INTERVAL 5 MINUTE) AS hour_market_naive
FROM raw_selected
WHERE intervention = 1;
