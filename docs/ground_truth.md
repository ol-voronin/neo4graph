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


## Oblast panel (added 2026-10-22, migrations 015/016, not yet applied)
Goal: the same prospective test for every oblast, not only Kharkiv. One row per oblast and Kyiv calendar day.

Sources, all of which exist for every oblast:
- **Oblast administration (OVA) daily summaries** -> table `oblast_daily` (`src/parse_oblast_daily.py`, channels in
  `src/oblasts.py`, collected by `collect.yml` into `data/ova/`, summary-like posts only, never into `messages`).
  Kharkiv keeps its exact weapon-block parser (`parse_kharkiv_daily.parse`); the others use generic regexes
  (missiles, strike drones, FPV kept apart, KAB/air strikes, MLRS, artillery, "N ударів по M населених пунктах",
  shot down, killed/injured). A scope guard drops reposts of national summaries (an OVA channel reposting "16
  ballistic missiles ... from Kharkiv to Volyn" must not give that oblast 16 missiles). Coverage reality, checked on
  2026-10-22 posts: only Kharkiv lists every weapon class daily (`complete = true`); Zaporizhzhia lists air strikes
  and UAVs but lumps Shaheds with FPV; Kherson, Dnipropetrovsk, Donetsk, Cherkasy post daily summaries mostly without
  missile/drone counts; most rear oblasts post per-attack messages. No verified official channel for Sumy and Rivne
  (t.me/sumy_oda is a Russian-side impostor). Kharkiv history is copied from `kharkiv_daily`
  (`db/backfill/015_kharkiv_to_oblast_daily.sql`).
- **LLM-extracted strike events** (`extracted_events`, view `region_daily_strikes`): per oblast, positive evidence.
- **Air-raid alerts** (`alerts`, view `oblast_alerts_daily`): count and minutes per oblast and day. Exposure proxy and
  feature only, never the label (see "Alerts" above).
View `oblast_daily_gt` puts them side by side (counts = max over sources).

Target `ob_massive` = missiles >= 7 or strike drones >= 20 in the oblast (owner's rule applied at oblast level, FPV
excluded); `ob_missile_day` = missiles > 0. A day is **positive** if any source shows it; **negative** only if a
complete OVA summary exists for that oblast and day, or the events extractor read at least `ee_min_extracted` posts of
that day (`extraction_coverage_daily`); otherwise **unknown** (NaN), never zero. Caveats: oblast, not city; extracted
events inherit the extractor's errors; incomplete OVA summaries can only add positives, so oblasts without a complete
source rely on extractor coverage for their negatives.

Model: `models/ua_oblast_massive_base_v1.json` (region indicators + per-region base rate shrunk toward the all-oblast
rate + own lags) vs `ua_oblast_massive_v1.json` (+ national `l2_ru` and the region-specific share of RU threat posts
naming a city of that oblast, `geo_regions()` from the KATOTTG gazetteer). Evaluation as for the Kharkiv models:
expanding window, block bootstrap over dates (all regions of a date stay together), within-region AUC next to the
pooled AUC (pooled AUC is inflated by base-rate differences between oblasts), and a shuffled-date control
(`nowcast.py backtest --model ua_oblast_massive_v1 --controls 50`). Nothing has been measured on real data yet; the
synthetic check (`python src/test_oblast_pipeline.py`) only proves the pipeline finds a planted signal and finds
nothing when there is none. The Kharkiv models keep running unchanged until the pooled model is validated.

Gazetteer: `src/build_gazetteer.py` downloads the newest KATOTTG codifier (Мінрозвитку) and writes
`geo/gazetteer_ua.json` (17.7k names -> oblast + category) and the generated migration `016_rule_geo.sql`
(`geo_regions(text)`, view `rule_labels_geo`). `rule_labels` is not changed: its six-city `r_target` feeds frozen specs.
`local/gazetteer.json` stays for the local Kharkiv extractor (its district names are villages elsewhere in Ukraine).
