-- Zone x day sufficient statistics from the zone-hour panel, summed over bases.
-- Every analysis quantity is a ratio of these sums, so any further aggregation
-- (zones into groups, days into weeks) is a plain sum followed by the same ratios.
WITH p AS (
    SELECT *
    FROM read_parquet('${panel_glob}', hive_partitioning = false)
)
SELECT
    pu_zone                                                AS zone_id,
    CAST(hour_ts AS DATE)                                  AS day,
    sum(n_trips)                                           AS n_trips,
    sum(n_airport_trips)                                   AS n_airport_trips,
    sum(n_shared_request)                                  AS n_shared_request,
    sum(n_wait_valid)                                      AS n_wait_valid,
    sum(coalesce(mean_wait_seconds, 0) * n_wait_valid)     AS wait_sum,
    sum(coalesce(total_base_fare, 0))                      AS fare_sum,
    sum(coalesce(total_driver_pay, 0))                     AS pay_sum,
    sum(coalesce(mean_tips, 0) * n_trips)                  AS tips_sum,
    sum(coalesce(mean_cbd_congestion_fee, 0) * n_trips)    AS cbd_fee_sum
FROM p
GROUP BY 1, 2
ORDER BY 1, 2
