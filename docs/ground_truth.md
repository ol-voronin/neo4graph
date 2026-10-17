# Ground truth: what we have and what we do not

## What the Air Force (kpszsu) summaries give
Parsed by `src/parse_kpszsu.py` into `waves` (one row per night/day summary):
launched drones, launched missiles (when stated with a number), S-8000 "Banderol" count,
shot-down/suppressed total, number of hit locations, number of debris locations.
These are **nationwide** totals and **self-reported by the Ukrainian side**, preliminary and later revised.
Flag `missiles_unspecified = true` means the post mentions missile types without numbers: the count is a lower bound.

## The gap: "7-10 missiles / 20 drones on a single city"
Summaries do **not** give per-city numbers. The project owner's definition cannot be computed from them directly.
Options, from cheapest to best:

1. **Two-level definition (recommended to start).**
   - Level N (nationwide): "massive" = launched missiles >= X OR drones >= Y per night. X, Y chosen from the
     observed distribution (see `analysis/` once history is loaded), fixed BEFORE looking at rhetoric.
   - Level K (Kharkiv city/oblast): the owner's per-city rule applied from Kharkiv OVA / Synehubov posts and
     the Kharkiv monitoring feeds (`tlknewsua`, `monitor1654`), where per-target reports exist.
2. **Tracking posts.** kpszsu posts "BpLA na <city>" many times per night. Counting posts per city is a noisy
   proxy for drones heading to the city, NOT a count of drones. Use only as a feature, never as the label.
3. **External event data.** ACLED (free registration, geocoded, lagged by days) gives per-location strike events.
   Kaggle "Massive Missile Attacks on Ukraine" (community dataset) can be downloaded manually and loaded into `waves`
   for 2022-2025 history; URL not verified from this environment.

## History backfill
- `t.me/s/<channel>?q=` search returns only ~20 results, so it cannot backfill years.
- Plain pagination reaches ~320 kpszsu posts per 40 pages (the channel posts ~300 per day), so full history by
  scanning is too slow. A secant/interpolation crawler on message ids (3-4 requests per day) is feasible as a long
  GitHub Action job (about 1700 days). Until then, history = ACLED/Kaggle + the sirens dataset.

## Alerts (proxy, not strikes)
`alerts` holds oblast-level sirens 2022-02-25 .. 2026-10-17 (volunteer data) plus raion/hromada-level official data.
Alerts show threat presence, not impact. Do not use them as the "massive strike" label.


## Kharkiv daily layer (added 2026-10-17)
`src/parse_kharkiv_daily.py` parses the governor's daily summary (@synegubov, "Протягом минулої доби ...") into
`kharkiv_daily`: oblast-level counts of missiles / guided bombs / MLRS / drones / FPV for the previous day, plus
whether the city of Kharkiv and which districts were attacked.
Caveat: counts are for the whole oblast, not the city. City-level massed day is approximated as
`city_attacked AND (missiles >= 7 OR drones >= 20)`; sensitivity run: missiles 7..10. "БпЛА (тип встановлюється)" is
included in `drones`; FPV and guided bombs are kept separate and are not counted toward the massed definition.
Operational feeds (tlknewsua, monitor1654) are tracking posts ("на Липці", "Италмас на ...") with heavy repeats and
are used as features, not as ground truth.
