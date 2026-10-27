# Oblast model v5: explosions tomorrow, trained on 2022–2026, with the monitors as precursors (pre-registered 2026-10-27)

Written before any v5 number was computed. Owner's request (2026-10-27): more sources for the forecast. What the
inventory showed first:

- the live collection has ~95 channels, the frozen rhetoric panel has 12 (docs/forecast_v4.md keeps them);
- the six Russian official / state channels that are collected but not in the panel (mod_russia, news_kremlin,
  medvedev_telegram, tass_agency, rian_ru, SolovievLive) have 2–6 weeks of posts in Neon: nothing to train on yet.
  Collecting their t.me/s history (history.yml, a separate channel set so that the frozen 12-channel aggregates and the
  weekly test stay untouched) is the next step; they get a version of their own (v6) when it is in;
- v4 trained from 2026-05-30 only, while the 12-channel history Parquet (2022-02-22..2026-03-31) and the monitors'
  Parquet (2022-03-29..2026-10-23) exist, and the v4 label `ob_expl` already covers 2022-03-29 onwards;
- the four monitoring channels (povitryanatrivogaaa, eRadarrua, war_monitor, vanek_nikolaev) are read only for the
  label; their posts before the cutoff — strategic aviation, carriers at sea, warnings, and the explosions reported so
  far that day — are the obvious real-time precursors, and no model uses them.

So v5 is not "more channels": it is the same target with (a) four years of training rows, (b) the monitors as
features, (c) an evening issue at 21:00. The new channels come later as v6, when their history exists.

## Target and label (unchanged)
`ob_expl` as in v4 (src/explosions.py, explosion_daily): >= 1 monitor report of explosions naming the oblast on Kyiv day
D; known negative = no report while the feed was alive; Luhansk and Crimea get no negatives. 24 panel oblasts + Kyiv city.

## Features
Every feature is known before the cutoff on D-1 (features.py real-time rule). History aggregates come from the Parquets
through `src/hist5.py` into Neon (migration 038), the live range from `messages` with the same code (`history.rule_labels`
/ the SQL rule views are verified identical; `monitors.daily` is the one function for both).

- **Baseline** (`ua_oblast_expl_base_v5`): region indicators, shrunk region base rate, own target at D-2 and its 7-day
  mean, ACLED published-week rates (`acled_l`, `acled_m8`) — exactly v4's baseline, trained on the long frame.
- **Rhetoric** (12-channel panel, 24 h window ending at the cutoff): `l2_ru`, `tc_energy_ru`, `tc_dc_ru`, `tc_rail_ru`,
  `tc_vpk_ru` (national), `thr_geo_ru`, `tcg_ru` (per region) — the v4 signals, now with history from 2022-02.
- **Monitors** (4 channels, same window, `src/monitors.py`, counts as log1p):
  - `mon_posts` feed activity; `mon_strat` posts naming strategic aviation (Ту-95/160/22, Оленья, Енгельс, Шайковка,
    МіГ-31, Кинджал…); `mon_sea` carriers at sea (Калібр, носії, акваторія, фрегат, підводний…); `mon_warn` warnings
    (масований, суттєва загроза, попередження, Іскандер, балістика, комбінований…) — national;
  - `mon_reg` per region: explosion reports naming the region inside the window (what has already happened on D-1 up
    to the cutoff; a real-time quantity, so allowed).
- Each signal enters as the window value and its 3-day mean (`design_long`), as before.

## Models (models/*.json, frozen with this file)
| name | cutoff | deadline | signals | compare_to |
|---|---|---|---|---|
| ua_oblast_expl_base_v5 | 15 | 24 | none | — |
| ua_oblast_expl_rhet_v5 | 15 | 24 | rhetoric | base_v5 |
| ua_oblast_expl_mon_v5 | 15 | 24 | monitors | base_v5 |
| ua_oblast_expl_v5 | 15 | 24 | rhetoric + monitors | base_v5 |
| ua_oblast_expl_v5_e21 | 21 | 24 | rhetoric + monitors | base_v5 |

The baseline has no windowed feature, so one baseline serves both cutoffs. Logistic regression C = 1 on standardised
features, shrink 20, min_train 60 days, `history: true` (frame from 2022-04-01). Live predictions refit daily; the
backtest refits every 7 days (`refit_days`) and predicts the days in between with the last fit, for speed only.

## Protocol and decision rules
- Backtest: expanding window over 2022-04-01..2026-10-26 with the live code path; metrics on all scored rows and,
  for the comparison with v4, on rows from 2026-05-30 (`--since`). AUC, within-region AUC, Brier; Δ AUC candidate −
  baseline with the block bootstrap CI over dates; shuffled-date control (`--controls`) for the candidates.
- **Rhetoric** adds a signal only if Δ AUC(rhet_v5 − base_v5) > 0 with the 95% CI above 0 and control p < 0.05 — the
  v4 rule, now with four years of training. Otherwise the v4 verdict stands.
- **Monitors** earn the map only if Δ AUC(mon_v5 − base_v5) > 0 with the CI above 0; the within-region AUC must not
  fall. `mon_reg` is expected to carry most of it; that is fine, it is the point.
- **Evening issue**: `v5_e21` is reported against the same baseline; the question is how much the 21:00 window adds
  over 15:00 (Δ AUC e21 − v5 on common rows). It goes on the map next to the 15:00 number if the gain is above 0 with
  the CI above 0.
- The forecast page shows the v5 model that passes its rule; if none does, the page keeps v4 and this file records it.

## Known limits
- Monitor coverage changes over the years (eRadarrua starts 2022-12, the feed's volume grows); `mon_posts` is in the
  model so that the others are read relative to it, but the label's "alive" rule already conditions on volume.
- The rhetoric history was collected from t.me/s previews; deleted posts are missing. Same for the monitors.
- Nothing here is an air-raid warning. The monitors' own alerts are minutes ahead; this is a day ahead.

## Result (backtest 2026-10-27, written after the runs)
Rows: 38 395 known oblast-days 2022-06..2026-10-26 (12 718 explosion days); the 2026-05-30+ slice (3 459 rows, 1 357
positives) is the one comparable with the v4 result. `backtest_nowcast.json` copies in the session scratchpad.

| model | AUC since 2026-05-30 | within-region | Brier | Δ AUC vs base_v5 (95% CI) | AUC all years | Δ all years (CI) |
|---|---|---|---|---|---|---|
| ua_oblast_expl_base_v5 | 0.864 | 0.630 | 0.144 | — | 0.858 | — |
| ua_oblast_expl_rhet_v5 | 0.847 | 0.580 | 0.154 | −0.017 (−0.037 … +0.001) | 0.856 | −0.002 (−0.006 … −0.000) |
| ua_oblast_expl_mon_v5 | 0.866 | 0.619 | 0.143 | +0.002 (−0.007 … +0.010) | 0.859 | +0.001 (−0.001 … +0.002) |
| ua_oblast_expl_v5 | 0.853 | 0.596 | 0.151 | −0.011 (−0.030 … +0.003) | 0.856 | −0.002 (−0.005 … +0.001) |
| ua_oblast_expl_v5_e21 | 0.855 | 0.601 | 0.150 | −0.009 (−0.025 … +0.004) | 0.856 | −0.002 (−0.005 … +0.001) |

- **Rhetoric: the v4 verdict stands with four years of training.** Δ AUC is negative in both slices; over all years the
  CI is entirely below 0. Shuffled-date controls were not run: a negative Δ cannot pass the rule.
- **Monitors: no signal a day ahead.** Δ +0.002 with the CI across 0, and the within-region AUC falls (0.630 → 0.619).
  Univariate, `mon_reg` has AUC 0.76 pooled but 0.58 within a region: it says *which* oblasts get hit, which the base
  rate and ACLED already say, not *when*. `mon_strat`, `mon_sea`, `mon_warn` are at 0.46–0.52 alone: posts naming
  aviation or carriers before 15:00 (or 21:00) on D-1 do not separate D's explosion days from the others.
- **Evening issue: nothing to gain at 21:00.** e21 is within 0.002 of the 15:00 model on every metric.
- **History training does not move the baseline**: base_v5 0.864 vs base_v1 0.866 on the same slice (v4 doc). The
  oblast's own recent days and ACLED carry the predictable part; 2022–2025 adds rows, not information about 2026.
- Decision: **no v5 model earns the map.** The page keeps v4; the v5 models keep issuing frozen forecasts daily (the
  cron at 13:10 UTC covers their 15:00 window, 19:10 UTC the 21:00 one) so that the prospective record accumulates.

Exploratory, after the result (not pre-registered, not a verdict): `ua_oblast_expl_mon_v5_e23`, the monitors-only model
with the window ending 23:00 on D-1, to see whether the monitors' evening feed carries anything at all for D.

| model | AUC since 2026-05-30 | within-region | Δ AUC vs base_v5 (CI) | AUC all years | within-region | Δ all years (CI) |
|---|---|---|---|---|---|---|
| ua_oblast_expl_mon_v5_e23 | 0.868 | 0.631 | +0.004 (−0.005 … +0.012) | 0.860 | 0.722 | +0.002 (+0.000 … +0.004) |

A hint, not a result: the late window is the first monitor variant whose all-years CI stays above 0, and the within-region
AUC rises (0.715 → 0.722), but the gain is two thousandths. If it is worth anything, it is as a late *update* of the
15:00 number from the evening feed, issued on the VPS (the GitHub cron cannot hit a 23:00–24:00 Kyiv window reliably),
and it needs a pre-registered spec of its own before it is believed.

Verification of the aggregates (`hist5.py verify`): on the monitors' overlap days (2026-10-17..23) the Parquet holds
~18% more posts than the live collection (4 720 vs 3 991; explosion reports 605 vs 505), i.e. the t.me/s preview is the
more complete source and the live feed under-collects; the feature definitions agree post for post. The rhetoric
overlap is the live panel's first three days, which are partial by construction.
