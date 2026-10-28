-- Fixed-seed stratified trip sample for the ML models.
-- Strata: base x pickup borough x pickup hour-of-day. Up to ${rows_per_stratum} rows per
-- stratum, chosen by a deterministic hash of the file row number and the seed, so the
-- same file and seed always yield the same sample. sample_weight = stratum size / rows kept.
COPY (
    WITH keyed AS (
        SELECT
            tr.trip_row,
            tr.base,
            coalesce(zp.borough, 'Unknown')             AS pu_borough,
            hour(tr.pickup_datetime)                    AS pickup_hour,
            count(*) OVER (
                PARTITION BY tr.base, coalesce(zp.borough, 'Unknown'), hour(tr.pickup_datetime)
            )                                           AS n_stratum
        FROM trips tr
        LEFT JOIN zones zp ON tr.pu_zone = zp.zone_id
        WHERE tr.pickup_datetime >= TIMESTAMP '${month_start}'
          AND tr.pickup_datetime <  TIMESTAMP '${month_end}'
    ),
    picked AS (
        SELECT trip_row, n_stratum
        FROM keyed
        QUALIFY row_number() OVER (
            PARTITION BY base, pu_borough, pickup_hour
            ORDER BY hash(trip_row * 2654435761 + ${seed}), trip_row
        ) <= ${rows_per_stratum}
    )
    SELECT
        '${month}'                                                   AS month,
        tr.*,
        zp.borough                                                   AS pu_borough,
        zp.is_cbd                                                    AS pu_is_cbd,
        zp.is_airport                                                AS pu_is_airport,
        zd.borough                                                   AS do_borough,
        zd.is_cbd                                                    AS do_is_cbd,
        zd.is_airport                                                AS do_is_airport,
        p.n_stratum,
        p.n_stratum / least(p.n_stratum, ${rows_per_stratum})::DOUBLE AS sample_weight
    FROM trips tr
    JOIN picked p USING (trip_row)
    LEFT JOIN zones zp ON tr.pu_zone = zp.zone_id
    LEFT JOIN zones zd ON tr.do_zone = zd.zone_id
    ORDER BY tr.trip_row
) TO ${out_path} (FORMAT PARQUET, COMPRESSION ZSTD);
