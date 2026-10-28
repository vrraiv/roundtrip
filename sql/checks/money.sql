-- Fare and pay sanity: non-negative, 99.9th percentile within configured bands,
-- and the share of trips flagged on any money field.
WITH t AS (
    SELECT
        count(*) AS n,
        count(*) FILTER (WHERE base_passenger_fare < 0) AS n_neg_fare,
        count(*) FILTER (WHERE driver_pay < 0) AS n_neg_pay,
        count(*) FILTER (WHERE tips < 0 OR tolls < 0) AS n_neg_tips_tolls,
        count(*) FILTER (
            WHERE base_passenger_fare < 0 OR driver_pay < 0 OR tips < 0 OR tolls < 0
               OR base_passenger_fare IS NULL OR driver_pay IS NULL
        ) AS n_flagged,
        quantile_cont(base_passenger_fare, 0.999) AS fare_p999,
        quantile_cont(driver_pay, 0.999) AS pay_p999
    FROM trips
)
SELECT 'money_fare_nonnegative' AS check_name, 'hard' AS severity,
       n_neg_fare <= ${max_flagged_share} * n AS passed,
       n_neg_fare / n AS metric, ${max_flagged_share} AS threshold,
       format('{:,} of {:,} trips with negative base fare', n_neg_fare, n) AS detail
FROM t
UNION ALL
SELECT 'money_pay_nonnegative', 'hard',
       n_neg_pay <= ${max_flagged_share} * n,
       n_neg_pay / n, ${max_flagged_share},
       format('{:,} of {:,} trips with negative driver pay', n_neg_pay, n)
FROM t
UNION ALL
SELECT 'money_tips_tolls_nonnegative', 'soft',
       n_neg_tips_tolls <= ${max_flagged_share} * n,
       n_neg_tips_tolls / n, ${max_flagged_share},
       format('{:,} of {:,} trips with negative tips or tolls', n_neg_tips_tolls, n)
FROM t
UNION ALL
SELECT 'money_fare_p999_in_band', 'soft',
       fare_p999 BETWEEN ${fare_p999_min} AND ${fare_p999_max},
       fare_p999, ${fare_p999_max},
       format('base fare p99.9 = {:.2f}; band [{}, {}]', fare_p999, ${fare_p999_min}, ${fare_p999_max})
FROM t
UNION ALL
SELECT 'money_pay_p999_in_band', 'soft',
       pay_p999 BETWEEN ${pay_p999_min} AND ${pay_p999_max},
       pay_p999, ${pay_p999_max},
       format('driver pay p99.9 = {:.2f}; band [{}, {}]', pay_p999, ${pay_p999_min}, ${pay_p999_max})
FROM t
UNION ALL
SELECT 'money_flagged_share', 'hard',
       n_flagged <= ${max_flagged_share} * n,
       n_flagged / n, ${max_flagged_share},
       format('{:,} of {:,} trips flagged on a money field', n_flagged, n)
FROM t;
