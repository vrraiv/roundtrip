-- TLC taxi zone lookup (LocationID, Borough, Zone, service_zone) plus study flags.
-- ${cbd_zones} and ${airport_zones} are comma-separated LocationIDs from config.
CREATE OR REPLACE TABLE zones AS
SELECT
    CAST(LocationID AS INTEGER)                      AS zone_id,
    CAST(Borough AS VARCHAR)                         AS borough,
    CAST(Zone AS VARCHAR)                            AS zone_name,
    CAST(service_zone AS VARCHAR)                    AS service_zone,
    CAST(LocationID AS INTEGER) IN (${cbd_zones})    AS is_cbd,
    CAST(LocationID AS INTEGER) IN (${airport_zones}) AS is_airport
FROM read_csv(${csv_path}, header = true, all_varchar = true);
