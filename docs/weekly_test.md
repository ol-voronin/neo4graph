# Weekly oblast test: does RU panel rhetoric precede strike escalation? (pre-registered 2026-10-23)

Written before any weekly result was looked at. Code: `src/weekly.py`. Data: `acled_weekly` (ACLED aggregated export,
Ukraine, admin1 = oblast) and `rhetoric_weekly` (`src/history.py weekly`: 12 panel channels, history Parquet before
2026-03-31, live posts after).

## Unit and target
- Unit: (oblast, ACLED week W), 25 oblasts incl. Kyiv city, Crimea excluded; weeks from 2022-03-05 to the last ACLED week.
- `ev_W` = ACLED events in the oblast in week W of the sub-event types 'Air/drone strike', 'Shelling/artillery/missile
  attack', 'Disrupted weapons use' (interceptions).
- **Primary target `esc`**: escalation week, `ev_W >= max(3, 1.5 * mean(ev_{W-1} .. ev_{W-8}))` (relative to the oblast's
  own recent level, so the test is not won by telling Kharkiv from Zakarpattia).
- Secondary target `rear`: `ev_W >= 1` in a week when the 8-week mean is below 1 (a strike where strikes are rare).

## Features (only what is known before week W starts, with ACLED's publication lag)
- Baseline: oblast indicators; log1p(ev) of weeks W-2, mean of W-2..W-5 and W-2..W-9 (ACLED is published ~1-2 weeks
  late, so W-1 is never used); national log1p(sum ev) of W-2.
- Candidate = baseline + RU panel rhetoric of week W-1 (posts Sat..Fri before W): `thr_ru`, `l2_ru` (national shares),
  `geo_ru`, `geo_thr_ru`, `geo_tcg_ru` (share of RU panel posts naming the oblast / threatening it / threatening its
  infrastructure), each also as the difference from its own 4-week mean before W-1.

## Protocol
- Expanding window by week, first forecast after 26 weeks of training; logistic regression C=1 on standardised features
  (the nowcast.py protocol).
- Metrics: AUC, mean within-oblast AUC, Brier. Δ AUC candidate - baseline on the same rows, 95% moving-block bootstrap CI
  over weeks (block 4 weeks, 1000 resamples).
- Shuffled control: rhetoric columns circularly shifted by k weeks (k >= 4, 50 random k); p = share of shifts with Δ AUC
  >= the real one.
- Reported separately for the pre-panel history and the live panel period, and with / without the occupied or frontline
  oblasts (Luhansk, Donetsk).

## Decision rule
Rhetoric "adds a signal" only if Δ AUC > 0 with the 95% CI above 0 AND shuffled-control p < 0.05 on the primary target.
Anything else is reported as no evidence of a signal at weekly resolution. No feature or target is tuned after seeing
results; a change means a new pre-registered version.
