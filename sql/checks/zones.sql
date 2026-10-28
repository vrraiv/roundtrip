-- Pickup and dropoff zone IDs must exist in the TLC lookup table.
-- (The lookup includes 264 "Unknown" and 265 "N/A", so those pass; the share of
-- genuinely unknown IDs is what is checked.)
WITH t AS (
    SELECT
        count(*) AS n,
        count(*) FILTER (WHERE t.pu_zone IS NULL OR zp.zone_id IS NULL) AS n_unknown_pu,
        count(*) FILTER (WHERE t.do_zone IS NULL OR zd.zone_id IS NULL) AS n_unknown_do,
        count(*) FILTER (WHERE t.pu_zone IN (264, 265)) AS n_pu_unknown_zone_code
    FROM trips t
    LEFT JOIN zones zp ON t.pu_zone = zp.zone_id
    LEFT JOIN zones zd ON t.do_zone = zd.zone_id
)
SELECT 'zones_pickup_in_lookup' AS check_name, 'hard' AS severity,
       n_unknown_pu <= ${max_unknown_zone_share} * n AS passed,
       n_unknown_pu / n AS metric, ${max_unknown_zone_share} AS threshold,
       format('{:,} of {:,} pickups with zone not in lookup', n_unknown_pu, n) AS detail
FROM t
UNION ALL
SELECT 'zones_dropoff_in_lookup', 'hard',
       n_unknown_do <= ${max_unknown_zone_share} * n,
       n_unknown_do / n, ${max_unknown_zone_share},
       format('{:,} of {:,} dropoffs with zone not in lookup', n_unknown_do, n)
FROM t
UNION ALL
SELECT 'zones_pickup_unknown_code_share', 'soft',
       n_pu_unknown_zone_code <= 0.01 * n,
       n_pu_unknown_zone_code / n, 0.01,
       format('{:,} of {:,} pickups coded 264/265 (Unknown/N/A)', n_pu_unknown_zone_code, n)
FROM t;
