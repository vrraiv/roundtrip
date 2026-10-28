-- Timestamp sanity. Each row of the result is one check.
-- Flagged trips are not dropped from the panel's counts; they are excluded from
-- the wait-time averages. The hard threshold is on the *share* of flagged trips.
WITH t AS (
    SELECT
        count(*) AS n,
        count(*) FILTER (
            WHERE request_datetime IS NULL OR pickup_datetime IS NULL OR dropoff_datetime IS NULL
        ) AS n_null_ts,
        count(*) FILTER (WHERE pickup_datetime < request_datetime) AS n_pickup_before_request,
        count(*) FILTER (WHERE dropoff_datetime < pickup_datetime) AS n_dropoff_before_pickup,
        count(*) FILTER (
            WHERE pickup_datetime < TIMESTAMP '${month_start}'
               OR pickup_datetime >= TIMESTAMP '${month_end}' + INTERVAL ${slack_days} DAY
        ) AS n_pickup_outside_month,
        count(*) FILTER (
            WHERE request_datetime < TIMESTAMP '${month_start}' - INTERVAL ${slack_days} DAY
               OR dropoff_datetime >= TIMESTAMP '${month_end}' + INTERVAL ${slack_days} DAY
        ) AS n_request_dropoff_outside_window,
        count(*) FILTER (WHERE wait_seconds > ${max_wait_seconds}) AS n_wait_too_long,
        count(*) FILTER (WHERE duration_seconds > ${max_trip_hours} * 3600) AS n_trip_too_long
    FROM trips
)
SELECT 'ts_not_null' AS check_name, 'hard' AS severity,
       n_null_ts <= ${max_flagged_share} * n AS passed,
       n_null_ts / n AS metric, ${max_flagged_share} AS threshold,
       format('{:,} of {:,} trips missing a timestamp', n_null_ts, n) AS detail
FROM t
UNION ALL
SELECT 'ts_pickup_after_request', 'hard',
       n_pickup_before_request <= ${max_pickup_before_request_share} * n,
       n_pickup_before_request / n, ${max_pickup_before_request_share},
       format('{:,} of {:,} trips picked up before request', n_pickup_before_request, n)
FROM t
UNION ALL
SELECT 'ts_dropoff_after_pickup', 'hard',
       n_dropoff_before_pickup <= ${max_flagged_share} * n,
       n_dropoff_before_pickup / n, ${max_flagged_share},
       format('{:,} of {:,} trips dropped off before pickup', n_dropoff_before_pickup, n)
FROM t
UNION ALL
SELECT 'ts_pickup_within_month', 'hard',
       n_pickup_outside_month <= ${max_flagged_share} * n,
       n_pickup_outside_month / n, ${max_flagged_share},
       format('{:,} of {:,} pickups outside ${month} (+{} day)', n_pickup_outside_month, n, ${slack_days})
FROM t
UNION ALL
SELECT 'ts_request_dropoff_within_window', 'soft',
       n_request_dropoff_outside_window <= ${max_flagged_share} * n,
       n_request_dropoff_outside_window / n, ${max_flagged_share},
       format('{:,} of {:,} requests/dropoffs outside month +/- {} day', n_request_dropoff_outside_window, n, ${slack_days})
FROM t
UNION ALL
SELECT 'ts_wait_under_max', 'soft',
       n_wait_too_long <= ${max_flagged_share} * n,
       n_wait_too_long / n, ${max_flagged_share},
       format('{:,} of {:,} waits longer than {} s', n_wait_too_long, n, ${max_wait_seconds})
FROM t
UNION ALL
SELECT 'ts_trip_under_max', 'soft',
       n_trip_too_long <= ${max_flagged_share} * n,
       n_trip_too_long / n, ${max_flagged_share},
       format('{:,} of {:,} trips longer than {} h', n_trip_too_long, n, ${max_trip_hours})
FROM t;
