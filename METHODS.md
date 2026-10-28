# Methods notes

## Use of a coding agent

The engineering scaffold of this repository (phase 1: configuration, ingest, validation
suite, panel SQL, tests, workflows, this file) was drafted with Claude Code from a written
brief by the owner. The owner supplies the economic judgement: choice of identification
strategy, control groups, interpretation of estimates, and sign-off on every release. The
agent does not decide those; places where it would have had to are marked
`OWNER DECISION` in the code and listed in the README.

How agent-written code is validated:

- **Tests first-class.** Every transform has a test against synthetic data with known
  properties (`tests/conftest.py` generates a month of trips shaped like the TLC files, with
  parameterised corruptions). The validation suite is tested to fail on each corruption and
  to pass on clean data; the panel is checked against an independent pandas recomputation;
  the sample is checked for determinism and stratum caps.
- **Strict typing and lint** (`mypy --strict`, `ruff`) on every PR.
- **Real-data runs are reviewed by a human.** The backfill and monthly workflows print the
  validation report and profile to the job summary; the owner reads them before anything
  is mirrored into a release.
- **Facts checked against primary sources.** The HVFHV schema comes from TLC's data
  dictionary (March 2025); the CBD zone list from the MTA's published dataset
  (data.ny.gov `yfdc-w5jh`); both are cited in the code.
- **Phase 2 onward:** causal estimators will be tested on simulated panels with a known
  treatment effect, and the headline estimate compared with published benchmarks the owner
  supplies.

## Study design decisions (owner)

- **Anticipation window, 14 Nov 2024 to 4 Jan 2025.** Congestion pricing was announced
  (revived at $9) on 14 November 2024 and took effect on 5 January 2025. Behaviour in
  between may already reflect the policy, so those weeks are neither clean "before" nor
  "after". Owner design (2026-10-27): build a synthetic control for the CBD from zones
  outside it, fit on the period before the announcement, and forecast CBD outcomes over the
  window. The gap between actual and forecast is the anticipation effect; the event study
  then treats the window as its own period rather than folding it into the pre-period.
  The design is tested first on the same calendar window one year earlier (14 Nov 2023 to
  4 Jan 2024), when no policy change occurred: a good design forecasts that window with no
  systematic gap. Still to specify: outcome variables, daily versus zone-hour units, the
  donor pool (all non-CBD zones or Manhattan-only), and the estimator variant.

## Data handling choices (phase 1)

- Raw TLC files are streamed, hashed and discarded. The manifest records URL, size,
  SHA-256, ETag, row count, column types and null rates per month, so any derived output
  can be traced to exact bytes and reproduced by re-streaming from TLC.
- A month is skipped if its manifest entry matches the remote size and ETag. A mismatch is
  reported as a TLC revision and reprocessed only with `--force`; the previous hash is kept
  in `revision_of`.
- The panel keeps every in-month trip in the counts but excludes implausible values from
  the means they would corrupt (negative fares, waits above three hours). The share of such
  trips is a hard validation check, so silent degradation is not possible.
- Pickups outside the file's month are dropped from the panel (not from validation) so
  that months do not overlap.
- Schema checks compare type families (integer, float, timestamp, string) rather than exact
  widths, because TLC has said it may standardise parquet types across years. Exact types
  are recorded in the manifest so drift is visible.
- The trip sample is stratified by base × pickup borough × hour of day with a fixed cap per
  stratum and a deterministic hash order, and carries a sampling weight. Changing the seed
  changes the sample; nothing else does.
