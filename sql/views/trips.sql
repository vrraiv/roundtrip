-- Normalised view over one month of raw HVFHV trips (trips_raw).
-- Rendered by roundtrip.db.register_trips; ${base_case} maps licence numbers to base
-- names and ${cbd_fee_expr} is NULL for files that predate the 2025 column.
CREATE OR REPLACE VIEW trips AS
SELECT
    file_row_number                                   AS trip_row,
    hvfhs_license_num                                 AS base_code,
    ${base_case}                                      AS base,
    dispatching_base_num,
    originating_base_num,
    request_datetime,
    on_scene_datetime,
    pickup_datetime,
    dropoff_datetime,
    CAST(PULocationID AS INTEGER)                     AS pu_zone,
    CAST(DOLocationID AS INTEGER)                     AS do_zone,
    CAST(trip_miles AS DOUBLE)                        AS trip_miles,
    CAST(trip_time AS DOUBLE)                         AS trip_time,
    CAST(base_passenger_fare AS DOUBLE)               AS base_passenger_fare,
    CAST(tolls AS DOUBLE)                             AS tolls,
    CAST(bcf AS DOUBLE)                               AS bcf,
    CAST(sales_tax AS DOUBLE)                         AS sales_tax,
    CAST(congestion_surcharge AS DOUBLE)              AS congestion_surcharge,
    CAST(airport_fee AS DOUBLE)                       AS airport_fee,
    CAST(tips AS DOUBLE)                              AS tips,
    CAST(driver_pay AS DOUBLE)                        AS driver_pay,
    CAST(${cbd_fee_expr} AS DOUBLE)                   AS cbd_congestion_fee,
    shared_request_flag = 'Y'                         AS shared_request,
    shared_match_flag = 'Y'                           AS shared_match,
    access_a_ride_flag = 'Y'                          AS access_a_ride,
    wav_request_flag = 'Y'                            AS wav_request,
    wav_match_flag = 'Y'                              AS wav_match,
    -- request-to-pickup wait in seconds; NULL when either timestamp is missing
    CAST(epoch(pickup_datetime - request_datetime) AS DOUBLE) AS wait_seconds,
    CAST(epoch(dropoff_datetime - pickup_datetime) AS DOUBLE) AS duration_seconds
FROM trips_raw;
