# roundtrip

NYC ride-hail marketplace economics on live public data. A re-running monthly pipeline over
the TLC High Volume For-Hire Vehicle trip records (Uber and Lyft, 15–20 million trips a
month), with causal inference, heterogeneous-effect estimation and standard ML on top, and a
human review gate before anything is published.

**Status: phase 1 (scaffold, ingest, validation, zone-hour panel, CI).** No estimates yet.
The headline figure and three headline numbers with intervals arrive with phase 2.

| Phase | Scope | State |
|---|---|---|
| 1 | Config, ingest two months, validation suite, zone-hour panel, CI green | this commit |
| 2 | Congestion-pricing event study, diagnostics, first report, review gate, candidate PR end to end | next |
| 3 | Wait-time and driver-pay models with drift monitoring; uplift on an RCT | later |
| 4 | Optional: Chicago, weather, interactive site | maybe |

## Phase 1 on real data

Two months processed end to end on the standard GitHub Actions runner from the
`phase-1/scaffold` branch (runs 40607735699 and 40607914444), no R2 configured, outputs kept
as workflow artifacts:

| Month | Rows | Raw size | Wall time | Peak RSS | Validation | Schema | Panel | Sample |
|---|---|---|---|---|---|---|---|---|
| 2024-12 | 21,068,851 | 507 MB | 19 s | 2.3 GB | pass | hvfhv-2019 | 365,227 rows, 23 MB | 96,758 rows, 3.7 MB |
| 2025-01 | 20,405,666 | 491 MB | 23 s | 2.2 GB | pass | hvfhv-2025-cbd | 364,630 rows, 23 MB | 96,774 rows, 3.7 MB |

Well inside the 10 GB DuckDB cap, so a 44-month backfill is about 20 minutes of runner
time. At ~27 MB of derived output per month the R2 footprint for 2023-01 to date is about
1.2 GB, inside the 2 GB target; rounding panel floats or trimming columns would halve it if
needed later.

Things the reviewer should know from these two months:

- 1.2% of trips in every month checked have a pickup timestamp earlier than the request
  timestamp (221k of 18.5M in January 2023). This is a steady reporting quirk, and the
  affected trips are excluded from wait-time means. Owner decision: the check stays hard
  with its own 5% limit (`max_pickup_before_request_share`), so only a real break stops
  the run.
- `on_scene_datetime` and `originating_base_num` are null for about 25% of trips through
  February 2025 (Lyft did not report them). From March 2025 `on_scene_datetime` is filled
  for all but 4.5% of trips. The null-rate check treats a fall like this as a soft warning
  and a rise as a hard failure.
- In January 2025 the mean `cbd_congestion_fee` on pickups in the 38 CBD zones is $1.48
  (the per-trip charge is $1.50) versus $0.14 elsewhere, which is consistent with the MTA
  zone list used in `config.py`.

## What runs every month

1. **Discover.** Probe TLC's CDN for months not yet in the manifest (one HEAD request per
   month from the configured start month to today).
2. **Ingest.** Stream the month's parquet (~500–700 MB) to a transient cache, computing its
   SHA-256 as it streams. Record size, hash, ETag, row count and column types in
   `manifest.json`. The raw file is deleted at the end of the job; TLC is the archive.
3. **Validate.** Schema (versioned by month; `cbd_congestion_fee` appears in 2025), row count
   against the trailing-12-month median, null-rate bands against history, timestamp order
   and month membership, fare and pay sanity, zone IDs against the lookup, licence codes
   against known bases. Hard failures stop the run and open a GitHub issue. The report is
   written as JSON and Markdown and attached to the candidate PR.
4. **Build.** A zone-hour panel (pickup zone × hour × base: trip counts, waits, fares,
   surcharges, driver pay, tip rate, shared-ride shares) and a fixed-seed stratified
   trip-level sample with sampling weights, both as parquet.
5. **Record.** A `run.json` with git SHA, manifest hash, package versions and the full
   config. Derived outputs are mirrored to Cloudflare R2 under 2 GB total.
6. **Candidate PR.** `candidate: YYYY-MM` with the validation report and manifest. Phase 2
   adds the reports, estimate diffs against the last approved release, and the review
   record that a CI check requires before the `release` environment can approve.

Processing one month is one GitHub Actions job (4 cores, 16 GB; DuckDB capped at 10 GB
and 4 threads). The backfill workflow loops over a range of months sequentially and prints
peak RSS and wall time per month to the job summary.

## Data

- **TLC HVFHV trip records**, monthly parquet, 2023-01 onward (confirmed by the owner;
  `ROUNDTRIP_START_MONTH`). Fields: request, on-scene, pickup and dropoff
  times; pickup and dropoff zone; miles and time; base fare, tolls, surcharges, tips, driver
  pay; shared and accessible-vehicle flags; dispatching base.
- **TLC taxi zone lookup** (265 zones with borough).
- **CBD zones**: the 38 taxi zones in the MTA's Central Business District Taxi Zones
  dataset, hard-coded in `config.py` with the source noted. Owner to confirm boundary
  treatment before estimation.

Raw data never enters git or R2. Only derived outputs do.

## Review process

The owner (an economist) is the reviewer. Every candidate PR carries the validation report
and, from phase 2, side-by-side estimates against the last approved release, placebo
results, drift metrics and a plain-language list of what changed. Approval happens through
the GitHub `release` environment; the review record in `reviews/YYYY-MM.md` must be filled
in first, and `releases/index.json` records every approved run with git SHA, manifest hash,
reviewer and date. Rejections are recorded too. Git history should show, month by month,
what was looked at and when the answer was no.

## Reproduce locally

```bash
uv sync                                  # Python 3.12, all deps, dev tools
uv run pytest                            # synthetic-data tests, < 1 min
uv run roundtrip discover                # which months TLC has that we do not
uv run roundtrip process-month -m 2024-12 --keep-raw   # ~1 GB download, a few minutes
uv run roundtrip validate -m 2024-12     # re-run checks on the cached raw file
uv run roundtrip build-panel -m 2024-12  # rebuild panel and sample from the cache
uv run roundtrip sync                    # pull derived outputs from R2 (needs .env)
```

Outputs land under `data/derived/` (git-ignored). Set `ROUNDTRIP_DATA_DIR` to keep the
DuckDB file and parquet mirror out of a synced folder. Copy `.env.example` to `.env` for R2
access; the pipeline runs without it.

Validation thresholds, zone lists, dates, seed and sample size are all in
`src/roundtrip/config.py` and overridable via `ROUNDTRIP_*` environment variables
(nested fields use `__`, e.g. `ROUNDTRIP_VALIDATION__ROW_COUNT_TOLERANCE=0.2`).

## Layout

```
src/roundtrip/
  config.py      settings, zone definitions, thresholds
  db.py          DuckDB connection, SQL templates, trips/zones views
  ingest/        TLC probing and streaming download, manifest
  validate/      versioned schema, check runner, report
  features/      zone-hour panel and trip sample
  pipeline.py    per-month orchestration, run.json
  storage/r2.py  R2 mirror (boto3, S3 API)
  cli.py         typer CLI
  causal/ models/ uplift/ report/   phases 2–3
sql/
  views/         trips.sql, zones.sql
  checks/        timestamps, money, zones, bases
  panel/         zone_hour.sql, trip_sample.sql
tests/           synthetic month generator + 45 tests (schema, checks, panel, pipeline, CLI)
.github/workflows/
  ci.yml         ruff, mypy --strict, pytest on every PR
  backfill.yml   manual: process a range of months, one job per month, with profiling
  monthly.yml    weekly cron: discover, process, push to R2, open candidate PR
```

See [METHODS.md](METHODS.md) for how the code was produced and validated, including the use
of a coding agent.

## Owner decisions

Settled: data start month 2023-01; MIT licence; pickup-before-request check hard at 5%;
anticipation window 14 Nov 2024 to 4 Jan 2025 handled by a synthetic-control forecast
tested on the same window a year earlier; three comparisons for the event study (prior
year, synthetic control, June to November 2024 pre-window); the fare measure is the base
passenger fare, which excludes the congestion fee (details in METHODS.md);
R2 bucket and repo secrets in place
(the bucket secret may be named `R2_BUCKET` or `R2_BUCKET_NAME`, the workflows accept
either).

Still open:

- Whether to drop airport trips (default keeps them; `ROUNDTRIP_EXCLUDE_AIRPORT_TRIPS`).
- Benchmark estimates to compare against.
