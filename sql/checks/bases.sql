-- HVFHS licence numbers must map to known bases (config.base_codes). Unknown codes
-- are kept in the data as base = 'Other' and listed here, never dropped silently.
WITH t AS (
    SELECT
        count(*) AS n,
        count(*) FILTER (WHERE base = 'Other') AS n_unknown,
        coalesce(
            string_agg(DISTINCT base_code, ', ' ORDER BY base_code) FILTER (WHERE base = 'Other'),
            'none'
        ) AS unknown_codes
    FROM trips
)
SELECT 'bases_known_codes' AS check_name, 'hard' AS severity,
       n_unknown <= ${max_unknown_base_share} * n AS passed,
       n_unknown / n AS metric, ${max_unknown_base_share} AS threshold,
       format('{:,} of {:,} trips from unknown licence codes: {}', n_unknown, n, unknown_codes) AS detail
FROM t;
