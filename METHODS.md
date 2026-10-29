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
- **Phase 2:** the causal estimators are tested on a simulated zone-day panel with a known
  treatment effect (`tests/test_causal.py`): the synthetic control, the fixed-effects event
  study and the three comparisons each recover a 15% fall in trips and a 10% rise in waits
  within tolerance, the fake-date placebos return zero, and the 2023 test window returns
  a gap near zero. That test caught one real bug before any real data was touched: the
  donor adjustment subtracted percentage changes, which is biased when the common trend is
  large; it now divides ratios. The headline estimate is to be compared with published
  benchmarks the owner supplies (placeholder in the report).

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
  systematic gap. Specified by the owner: outcomes are daily trip counts, mean wait time
  and mean base fare; units are daily zone totals; the donor pool is non-CBD Manhattan plus
  the inner Brooklyn and Queens zones on the East River, airports excluded; the estimator
  is standard synthetic control with non-negative weights summing to one, fitted from
  January 2023 to 13 November 2024, with placebo-in-space inference.
- **Three comparisons for the main event study** (owner, 2026-10-27), all reported: the
  same weeks one year earlier; the synthetic control above; and the clean pre-event window
  from June 2024 to 13 November 2024. The brief's zone-based control groups (Manhattan north
  of 60th; inner Brooklyn/Queens) remain as the control units inside the fixed-effects
  event study.
- **Fare measure.** Outcomes use TLC `base_passenger_fare`, which excludes tolls, taxes,
  fees and the CBD congestion fee, so surcharge pass-through does not enter the fare
  outcome. The fee itself is reported separately.

## Estimation choices (phase 2)

- **Sufficient statistics.** Every analysis starts from zone x day sums (trips, waits,
  fares, pay, tips, shared requests). Outcomes are ratios of sums, so group and weekly
  aggregates are exact and consistent with each other.
- **Synthetic control scaling.** The CBD aggregate is far larger than any donor zone, so
  every daily series is divided by its own fit-window mean before weights are fitted, and
  the forecast is rescaled to CBD units. Weights are non-negative and sum to one (NNLS with
  a penalised sum-to-one row), fitted from the first day of data to 13 November 2024.
  Donors with more than 2% missing days are dropped. Placebo-in-space refits every donor
  as if treated; donors whose pre-period fit is more than five times worse than the CBD's
  are excluded from the ranking. The interval on the gap is the treated gap plus or minus
  1.96 placebo-gap standard deviations; the p-value is the rank of the CBD's post/pre RMSPE
  ratio. For trips the counterfactual is the *step-adjusted seasonally anchored* design
  described below (owner decision 13, 29 October 2026), fitted the same way and read in
  the same form; its placebos refit every donor under the same design. Waits and fares
  keep the simplex (decision 8).
- **The 2023 test.** The same design is fitted to 13 November 2023 and evaluated over
  14 November 2023 to 4 January 2024, when nothing happened. Its gap is reported next to
  the real one and bounds what the design can resolve.
- **Out-of-sample test.** Requested in the review of the 2026-08 release: each
  counterfactual model is trained on 1 January to 13 November 2023 only and forecasts the
  untreated year that follows with its parameters frozen. The adopted design for each
  outcome is judged on it (within 3% over the year and its last quarter); the others are
  reported. Trained to November 2023 the trips design cannot see the December 2023 break,
  so it fails this test for the reason the rolling-origin rule below allows for; the
  rule, not this test, selects the design, and the report says which it was.
- **Change-point test.** The log share of the CBD in the donor pool over the pre-policy
  period, and each forecast gap, is tested for a single mean shift against a constant,
  a line and a step plus trend (BIC; Quandt sup-F compared with every donor as a
  placebo). Run on 28 October 2026 it found a step in the week of 18 December 2023 and
  no trend, which is why no trend term enters the design.
- **Candidate trips designs.** Beside the simplex and ridge: *anchored* (the simplex
  weights plus the mean gap over the last 90 training days, a difference-in-differences
  on the synthetic control) and *growth-rate* (simplex weights fitted on week-over-week
  changes in log trips, the counterfactual a weighted geometric index of the donors
  pinned to the CBD's last-90-day log level). A free intercept and synthetic
  difference-in-differences were tried and dropped: the first reproduced the simplex,
  the second rested on ten effective days of time weights. Two *seasonally anchored*
  designs (owner decision, 29 October 2026) take the season out of the anchor: the
  simplex weights plus a calendar-month profile of the training residual over the last
  twelve training months and a level over the last 90 days net of that profile; the
  *step-adjusted* one first nets out a level shift inside the training window when the
  change-point test prefers a step there, so a break is not read as seasonality. The
  shift is measured on calendar months seen on both sides of the break in different
  years (the same-month difference), because within one year a step and a seasonal
  profile are not separable; with no such month the step is left alone.
- **Rolling-origin validation.** The selection rule for the trips design (owner decision,
  28 October 2026). Every candidate is trained from 1 January 2023 through the end of each
  quarter from June 2023 to September 2024 and scored on untreated days 1 to 3, 4 to 6 and
  7 to 12 months ahead, cut at 13 November 2024. Every donor is refitted as treated at
  every origin for the placebo share. A design passes only if its mean gap across origins
  is within 2 points at every horizon; RMSPE breaks ties. An origin counts toward the
  rule only if the level break of 18 December 2023 is identifiable in its training
  window, that is some calendar month is seen on both sides of it in different years,
  which holds from the March 2024 origin on (owner decision 12, 29 October 2026: an
  origin that trains before the break is asked to forecast a shift it cannot see; the
  7 to 12 month horizon then rests on one origin). The table over every origin is
  reported beside the rule. On 29 October 2026 (run 20261029T142313Z) the step-adjusted
  seasonally anchored design was the only one within 2 points at every horizon and was
  adopted (decision 13); the state-space fallback was not run. Waits and fares keep the
  simplex design.
- **Comparisons.** Prior-year and pre-window estimates divide the CBD's change by the
  donor pool's change (a difference-in-differences in logs). Intervals are 7-day block
  bootstraps (1000 replications, fixed seed). The post window is the first 364 days after
  5 January 2025 so the prior-year design never compares two treated days.
- **Event study.** Zone-week panel, log trips and level outcomes, zone and week fixed
  effects, standard errors clustered by zone (pyfixest, CRV1). The static specification
  has anticipation and post dummies with the clean pre-period as reference; the dynamic
  one has one dummy per relative week, binned at +/-30, reference = the last full week
  before the announcement. The pre-trend test is a joint Wald test on the clean leads.
  Placebos: three fake treatment dates in 2024 using only pre-announcement data; twenty
  draws of fake treated zones (a random third of the control group) with the true dates.
- **Not yet done from the brief:** causal-forest heterogeneity (econml) and hour-of-day
  splits. Both need a zone-boundary distance covariate from the shapefile and belong to a
  follow-up once the owner has read the first report.

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
- Null-rate bands are built from months that passed validation. A null rate above the
  band is a hard failure (data going missing); below the band is a soft warning, because
  that is what a reporting improvement looks like. Lyft began filling `on_scene_datetime`
  in March 2025, taking its null rate from about 25% to under 5%; the backfill stopped on
  that month until this rule was introduced.
- Schema checks compare type families (integer, float, timestamp, string) rather than exact
  widths, because TLC has said it may standardise parquet types across years. Exact types
  are recorded in the manifest so drift is visible.
- The trip sample is stratified by base × pickup borough × hour of day with a fixed cap per
  stratum and a deterministic hash order, and carries a sampling weight. Changing the seed
  changes the sample; nothing else does.
