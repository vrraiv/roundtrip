# Review record: synthetic control redesign

Eleven decisions on how to redesign the trips synthetic control, taken by the reviewer on 28 and 29 October 2026 after the 2026-08 release.

## Context

This is an ad hoc review, not tied to a monthly release. The 2026-08 release was approved
with a note: the CBD was losing trip share through 2024, which complicates the synthetic
control for trips. Trained on 2023 alone, the trips design over-predicts CBD trips by about
4% across the untreated year that followed, so the first-year trips estimate (-4.6 to -5.6%
across the three designs) cannot be separated from a decline that predates the charge.
Waits and fares pass the same test.

Two analyses ran on the full panel (January 2023 to August 2026) before the decisions
were taken: a change-point test on the CBD's share of trips
(run `20261028T215500Z`, [PR #17](https://github.com/vrraiv/roundtrip/pull/17)) and a
rolling-origin validation of four candidate trips designs
(run `20261029T014018Z`, [PR #18](https://github.com/vrraiv/roundtrip/pull/18)). Both are
in the technical report; this record keeps the decisions.

## What the tests found

- **A break, not a drift.** The CBD's share of donor-pool trips steps down by about 6
  points in the week of 18 December 2023 and is flat through 2024. A step fits better than
  a trend, and the step is larger than 88% of the donor zones show when tested the same way.
  A second step follows in the week of 24 November 2025, inside the treated period.
- **No candidate trips design passes the rolling-origin rule.** Each design was refit at
  six quarterly origins (June 2023 to September 2024) and scored 1 to 3, 4 to 6 and 7 to
  12 months ahead; the rule asks for a mean gap within 2 points at every horizon. All four
  are within 2 points at 1 to 3 months and none at 7 to 12 months.

  | Design | 1 to 3 months | 4 to 6 months | 7 to 12 months | Passes |
  |---|---|---|---|---|
  | Simplex (current) | -1.3 | -2.7 | -4.0 | no |
  | Ridge | -1.1 | -2.6 | -3.6 | no |
  | Anchored (last 90 days) | -0.4 | -1.6 | -3.4 | no |
  | Growth-rate | -1.0 | -2.0 | -3.6 | no |

- **Two causes.** The December 2023 step contaminates the long horizon: three of the four
  origins that score 7 to 12 months train before the step and forecast after it. And the
  gap has a seasonal shape the donors do not share, about 2 points either side of its
  yearly mean, high in summer and low in spring and autumn, the same in 2023 and 2024. The
  anchored design, the closest, fails because its level depends on the season of its last
  90 training days.
- **Waits and fares pass** every horizon under every design, with the largest mean gap
  under 1 point.

## Decisions

| # | Option | Recommendation | Decision |
|---|---|---|---|
| 1 | Run the change-point test and the intercept and SDID variants through the existing 2023 hold-out, with no design change | Done 28 Oct 2026, run `20261028T215500Z`: a break in the week of 18 Dec 2023, no trend; code in PR #17 | Yes |
| 2 | Adopt the rolling-origin protocol (six quarterly origins, June 2023 to September 2024; bias within 2 points at every horizon) as the selection rule for the trips design | Done 29 Oct 2026, run `20261029T014018Z`: no trips design within 2 points at every horizon; waits and fares pass; code in PR #18 | Yes |
| 3 | Estimators in the comparison: simplex, anchored (simplex plus the last-90-day level), ridge, growth-rate; intercept and SDID can be dropped (the first is the simplex, the second is fragile on daily data); state-space only if none passes | As listed | Yes |
| 4 | Allow a trend term inside the trips fit (not a post-hoc correction), validated like the weights | No: the test found a step and no slope once the step is allowed | No |
| 5 | Restrict or re-weight the donor pool toward office and visitor zones (Downtown Brooklyn, DUMBO, Long Island City, Brooklyn Heights, Williamsburg) | Try as one variant; too few zones to rely on alone | Later |
| 6 | Pull outside series (subway entries by station, TLC industry indicators, yellow-taxi CBD trips) to explain the 2024 decline, not as post-2025 controls | Yes, subway and TLC indicators first | Later |
| 7 | Meanwhile, present the first-year trips effect as a drift-bracketed range (-3.0 to -4.5%) instead of -4.6 to -5.6% | No; keep the current wording until step 3 is decided | No |
| 8 | Keep the waits and fares designs unchanged | Yes | Yes |
| 9 | Add a seasonally anchored trips design (calendar-month gap profile over the last twelve training months plus the latest level) and rerun the protocol | Yes, first: one branch and one run; it addresses the 2-point seasonal swing the validation exposed | Yes |
| 10 | State-space counterfactual (the fallback of row 3): donors as regressors with a local level | Later: only if row 9 fails; it adapts to the step like the anchored design but does not fix seasonality by itself | Yes |
| 11 | Wording now that the rule has run: keep -4.6 to -5.6% with the drift caveat, or move to the drift-bracketed range (-3.0 to -4.5%) | Keep the current wording through row 9; change it with the next release only if no design passes | Yes |

Rows 1 to 8 were decided on 28 October 2026 and rows 9 to 11 on 29 October 2026. A
"Yes" accepts the recommendation as written, including its conditions.

## What happens next

1. A seasonally anchored trips design is added and run through the rolling-origin rule
   (row 9).
2. If it fails, a state-space counterfactual is tried the same way (row 10).
3. The site keeps the trips effect at -4.6 to -5.6% with the drift caveat until a design
   passes; if none does, the wording changes with the next release (row 11).
4. The donor-pool variant and the outside series wait until then (rows 5 and 6).

## Reviewer

Initials: vrraiv
Date: 2026-10-29
