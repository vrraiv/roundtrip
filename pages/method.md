# How we measure it

This page is written by hand and reviewed like code; every number on the other pages is
computed. It explains what the estimates mean, what they rest on, and what they cannot say.

## The question

On 5 January 2025 New York began charging most vehicles $9 to enter Manhattan below 60th
Street, the Central Business District (CBD). Ride-hail trips that start, end or pass through
the zone pay $1.50 per trip. We ask what the charge did to three things a rider feels: how
many Uber and Lyft pickups there are in the CBD, how long a car takes to arrive, and what the
base fare is.

## The data

The NYC Taxi and Limousine Commission publishes every high-volume for-hire vehicle trip
(Uber and Lyft) each month: request, pickup and drop-off times, pickup and drop-off taxi
zones, fare components and driver pay, about 15 to 20 million trips a month. We use every
trip from January 2023 onward. The raw files are never stored by this project; TLC is the
archive, and each monthly file is checked against the record of its size and hash before use.

Trips are aggregated to pickup zone by day: counts, mean wait (request to pickup), mean base
fare and the other outcomes. The CBD is the 38 taxi zones in the MTA's zone list.

## Three comparisons

A change in the CBD after January 2025 is not by itself the effect of the charge; trips
were changing everywhere, with the seasons, the weather and the economy. Every estimate on
this site is therefore *relative to comparison zones*: the rest of Manhattan below 96th
Street and the inner Brooklyn and Queens zones across the East River, with the airports
left out. We use three ways of building the comparison, because each rests on a different
assumption and no single one is obviously right:

1. **Prior year.** The CBD in the first year of the charge against the CBD in the same
   weeks a year earlier, netted against the same change in the comparison zones.
2. **Synthetic control.** A weighted mix of comparison zones chosen to track the CBD
   closely before the charge; the effect is the gap between the CBD and that mix afterwards.
   For trips the mix also carries the CBD's own calendar pattern and allows for a fall in
   its share of trips in December 2023 (see below); for waits and fares the plain mix is used.
3. **Pre-window.** The CBD from June to 13 November 2024, just before the charge was
   confirmed, against the first year of the charge, again netted against the comparison zones.

The front page shows the range across the three. When they agree, the range is narrow.

## The anticipation window

The charge was confirmed on 14 November 2024 and started on 5 January 2025. Behaviour in
between may already have shifted, so that window is estimated separately and is not part of
"before" or "after".

## A break in trips, and the test the comparison must pass

Before trusting a comparison, we test it where we know the answer. The synthetic control is
fitted on 2023 alone and asked to forecast the year to 13 November 2024, when nothing
changed in policy. For waits and fares the forecast stays close to what happened. For trips
the original design did not: CBD pickups ran 4 to 6 percent below its forecast across that
untreated year. The reason turned out to be one event, not a slow drift. In the week of
18 December 2023, a year before the charge, the CBD's share of ride-hail pickups fell by
about 3 percent relative to the comparison zones and stayed there. A model fitted before
that week is asked to forecast a shift it cannot see, and a model fitted across it reads
the shift as part of the policy effect.

The trips design was therefore changed in October 2026, after a review recorded on the
Releases page. The comparison now carries the CBD's own calendar pattern (its gap to the
comparison zones has a seasonal shape they do not share) and nets out the December 2023
shift once it can see it. It is selected by a stricter test than the single untreated
year: each candidate design is refitted at the end of each quarter from June 2023 to
September 2024 and scored on the following 1 to 3, 4 to 6 and 7 to 12 months, all before
the charge was announced, and it passes only if its average miss is within 2 percentage
points at every horizon. A quarter-end counts in that test only when the December 2023
shift can be told from the seasons in the data up to it, which is from March 2024 on; the
earlier quarter-ends are reported alongside for the record. The step-adjusted design is
the only one of six candidates that passes. The trips page shows the test; the technical
report has every candidate.

What this means for the numbers: the synthetic control is the one comparison for trips
that is tested out of sample, and the only one built to net out the December 2023 shift.
The prior-year and pre-window comparisons rest on 2024 baselines, after that shift. The
intervals do not cover the choice of comparison, which is why all three are shown.

## What this cannot say

- Nothing here is about taxis, private cars, transit or traffic speeds; only ride-hail.
- "Relative to comparison zones" means the comparison zones may themselves have been
  affected (a trip that moved from the CBD to just outside it counts twice). The estimates
  are of the difference, not of the city-wide total.
- The base fare excludes the $1.50 CBD fee, tolls, tips and surcharges.
- The intervals are statistical. They do not cover the choice of comparison, which is why
  three are shown.

## For urbanists

Congestion pricing is the first in the United States and one of a handful worldwide (London
2003, Stockholm 2006, Singapore 1975). Most published evaluations look at traffic volumes,
speeds and transit ridership; this site looks only at the ride-hail market, where the charge
is a per-trip fee passed to the rider. The MTA's own
[reports](https://new.mta.info/project/CBDTP) cover the wider picture.

## For data and ML people

What is unusual here: the whole pipeline re-runs monthly on public data in a standard GitHub
Actions runner, with DuckDB as the engine and no servers; the counterfactual model is tested
out of sample, on an untreated year and then at six rolling origins, before any effect is
claimed, and the first trips design failed that test, which was published and led to the
redesign recorded on the Releases page; and nothing reaches this site until an economist
has approved the run, with the review recorded alongside the code. The code was
produced with a coding agent (Claude) under the owner's direction and review; the
repository's METHODS.md says how.
