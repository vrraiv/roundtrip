-- Zone-hour panel for one month: pickup zone x pickup hour x dispatching base.
-- Trips with pickups outside the month are excluded so months do not overlap.
-- Flagged trips stay in the counts but are excluded from the means they would corrupt.
COPY (
    WITH t AS (
        SELECT
            tr.*,
            date_trunc('hour', pickup_datetime) AS hour_ts,
            wait_seconds IS NOT NULL
                AND wait_seconds >= 0
                AND wait_seconds <= ${max_wait_seconds}                    AS wait_ok,
            base_passenger_fare IS NOT NULL AND base_passenger_fare >= 0  AS fare_ok,
            driver_pay IS NOT NULL AND driver_pay >= 0                    AS pay_ok,
            coalesce(zp.is_airport, false) OR coalesce(zd.is_airport, false) AS airport_trip
        FROM trips tr
        LEFT JOIN zones zp ON tr.pu_zone = zp.zone_id
        LEFT JOIN zones zd ON tr.do_zone = zd.zone_id
        WHERE pickup_datetime >= TIMESTAMP '${month_start}'
          AND pickup_datetime <  TIMESTAMP '${month_end}'
    )
    SELECT
        '${month}'                                                          AS month,
        pu_zone,
        hour_ts,
        base,
        count(*)                                                            AS n_trips,
        count(*) FILTER (WHERE airport_trip)                                AS n_airport_trips,
        count(*) FILTER (WHERE shared_request)                              AS n_shared_request,
        count(*) FILTER (WHERE shared_match)                                AS n_shared_match,
        count(*) FILTER (WHERE wav_match)                                   AS n_wav,
        count(*) FILTER (WHERE wait_ok)                                     AS n_wait_valid,
        avg(wait_seconds)            FILTER (WHERE wait_ok)                 AS mean_wait_seconds,
        quantile_cont(wait_seconds, 0.5) FILTER (WHERE wait_ok)             AS median_wait_seconds,
        avg(base_passenger_fare)     FILTER (WHERE fare_ok)                 AS mean_base_fare,
        avg(congestion_surcharge)                                           AS mean_congestion_surcharge,
        avg(cbd_congestion_fee)                                             AS mean_cbd_congestion_fee,
        avg(tolls)                                                          AS mean_tolls,
        avg(airport_fee)                                                    AS mean_airport_fee,
        avg(tips)                                                           AS mean_tips,
        sum(tips) FILTER (WHERE fare_ok)
            / nullif(sum(base_passenger_fare) FILTER (WHERE fare_ok), 0)    AS tip_rate,
        avg(driver_pay)              FILTER (WHERE pay_ok)                  AS mean_driver_pay,
        avg(trip_miles)                                                     AS mean_trip_miles,
        avg(trip_time)                                                      AS mean_trip_seconds,
        sum(base_passenger_fare)     FILTER (WHERE fare_ok)                 AS total_base_fare,
        sum(driver_pay)              FILTER (WHERE pay_ok)                  AS total_driver_pay,
        sum(trip_miles)                                                     AS total_trip_miles
    FROM t
    GROUP BY ALL
    ORDER BY pu_zone, hour_ts, base
) TO ${out_path} (FORMAT PARQUET, COMPRESSION ZSTD);
