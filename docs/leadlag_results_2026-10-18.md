# Lead-lag test 1: rule-based rhetoric vs Kharkiv strike intensity (2026-03-31 … 2026-10-17)

Data: fixed panel of 12 deep-backfilled channels (RU: MID_Russia, rybar, dva_majors, boris_rozhin, voenkorKotenok,
sashakots, wargonzo, RVvoenkor, meduzalive; UA: V_Zelenskiy_official, GeneralStaffZSU, ukrpravda_news), 201 days,
mean 278 RU / 49 UA posts per day. Target: Kharkiv oblast intensity index from the governor's daily summaries,
`idx = log1p(10*missiles + drones + guided_bombs)`, std 0.686, autocorrelation lag1 0.23. Signals: daily shares of
rule-flagged posts (`r_threat`, `rule_threat_level>=2`, `r_target`) by side, posting volume, RU-MoD-claimed
Ukrainian drone attacks. Method: ADF -> difference if needed; circular-shift permutation cross-correlation with
max-|r| family-wise correction over lags 1..3; Granger F-tests; expanding-window out-of-sample test (Ridge on own
lags/means +- signal lags 1..3, RMSE, moving-block bootstrap CI) and logistic AUC on the top-quartile day.

## Result

| signal | lag1 r (p_fw) | lag3 r (p_fw) | OOS RMSE delta [CI] | AUC delta [CI] |
|---|---|---|---|---|
| tgt_ru (RU posts naming a target) | +0.30 (0.026) | +0.35 (0.003) | -0.011 [-0.018, -0.003] | -0.019 [-0.069, +0.026] |
| l2_ru (RU threat+target/time) | +0.25 (0.067) | +0.18 (0.29) | -0.002 [-0.005, +0.001] | -0.018 [-0.061, +0.024] |
| thr_ru | +0.20 (0.22) | +0.01 (1.0) | -0.002 | +0.002 |
| thr_ua / l2_ua | +0.07 / +0.16 (0.68 / 0.09) | +0.09 / +0.02 | 0.000 / -0.001 | -0.026 / -0.008 |
| posts_ru (differenced) | +0.00 | -0.21 (0.015) | +0.004 | +0.004 |
| ru_drones (UA attacks on RU) | no lead (earlier run: -0.13/+0.15/-0.11, p_fw >= 0.2) | | | |

Only `tgt_ru` survives the permutation test (also Bonferroni over 7 signals at lag 3). But:

- **Reverse direction is stronger:** idx(t-1) -> tgt_ru(t) r = +0.43 (p_fw < 0.001); same-day r = +0.33. Granger is
  significant both ways. Russian channels name targets *after* strikes (reporting), and strike campaigns on Kharkiv
  run for several days, so the apparent lead is mostly campaign persistence plus reporting, not a warning.
- **Out of sample it is worth almost nothing:** RMSE 0.663 -> 0.653 (-1.5%, CI excludes 0); no gain on the
  top-quartile day. The baseline itself barely beats the unconditional std (0.663 vs 0.686): day-to-day intensity is
  close to unpredictable from these series.
- **Unstable:** first half corr(tgt_ru[t-1], idx[t]) = +0.21, second half +0.05.

Threat rhetoric as such (thr_*, l2_*), Ukrainian official rhetoric, posting volume and Ukrainian attacks on Russia
show no lead at the daily level.

## What this does and does not say

It does not say rhetoric is unrelated to strikes: there is co-movement at the campaign scale (weeks). It says the
daily rule-based shares of these 12 channels do not forecast the next day's intensity in Kharkiv beyond what
yesterday already tells you. The pre-registered success criterion (AUC gain with CI above 0) is not met.

## Next tests (in order)

1. Weekly / 3-day aggregation: does rhetoric lead at the campaign scale? (Fewer points: ~29 weeks, low power.)
2. Target restricted to missiles + guided bombs (drones are daily background in Kharkiv) and nationwide waves.
3. LLM `threat_level` instead of regex flags, once coverage of the panel reaches ~30% of posts (Gemini quota).
4. Narrative-level features (novelty, specific narratives) rather than coarse shares.
5. Operational early-warning posts (monitoring channels) as a separate, non-rhetorical baseline; if that is the
   only thing that predicts, the rhetoric hypothesis is answered.

## Test 1 (weekly / 3-day aggregation) — run 2026-10-18 evening

Weekly (n=29): no consistent lead for any signal (p_fw 0.13–1.0; one isolated thr_ua lag-1 vs missiles+KAB r=+0.51
p=0.04 among 20 tests, treated as chance). 3-day blocks (n=67): same, scattered signs. Too few points to say more.

## Test 2 (target without drones: missiles + guided bombs; and binary "missile day")

`idx_mk = log1p(10*missiles + kab)`, and `missile_day = missiles > 0` (90/201 days).

- Cross-correlation lag 1: tgt_ru +0.27 (p_fw 0.02), l2_ru +0.24 (0.13) for idx_mk; for missile_day tgt_ru +0.29
  (0.01), l2_ru +0.32 (0.02), thr_ru +0.27 (0.07). The reverse direction is now weaker than forward
  (idx_mk -> tgt_ru +0.25, p 0.08), unlike the drone-inclusive index.
- Out-of-sample missile_day (expanding logistic, n_test 171, 84 positives; baseline = own lags/means):
  baseline AUC 0.683, Brier 0.234 (naive 0.250). Adding yesterday's rhetoric share:
  thr_ru +0.031 [-0.003, +0.076], l2_ru +0.026 [-0.004, +0.062], tgt_ru +0.012, l2_ua +0.018. Brier improves
  0.234 -> 0.224. Halves (l2_ru): base 0.58/0.59, full 0.59/0.65 — the gain sits in the second half.

Reading: on missile days (not drones) Russian threat rhetoric the day before carries a small, consistent,
borderline signal: +0.03 AUC, CI touching zero. Not yet the pre-registered success, but the first result that
points the right way in direction, consistency across related signals, and out-of-sample behaviour. Target for
the next round: missile days, not the drone-inclusive index.

## Test 3 (LLM threat labels instead of regex flags) — run 2026-10-19 morning, on a 20% sample

Labels: Haiku 5.5 batch, 5,501 of the 26,848 rule-flagged panel posts (the batch finished, but the company key was
disabled before the rest was downloaded; the remainder is unreachable with a key from another organisation).
Coverage: 177 of 201 days, mean 28 labeled RU posts per day, of which ~2.6 are LLM `threat_level >= 2`.
Features: `p2_ru` = share of rule-flagged posts the LLM confirms as threat (daily rule precision);
`llm_thr_ru = thr_ru * p2_ru` (estimated share of all RU posts that are real threats); `llm_l2_ru` (threat with
target/timeframe, not scheduled context), `llm_t3_ru` (threat >= 3), `llm_rh_ru` (rhetoric, not operational).
Protocol as test 2 (missile_day, expanding logistic, baseline = own lags; 171 test days, 84 positives).

| signal (lag 1 + 3-day mean) | AUC | Δ vs baseline 0.685 [block-bootstrap CI] | Brier |
|---|---|---|---|
| thr_ru (rule share, reference) | 0.708 | +0.023 [-0.016, +0.070] | 0.229 |
| l2_ru (rule) | 0.714 | +0.030 [-0.004, +0.066] | 0.226 |
| p2_ru (LLM precision of the rule) | 0.673 | -0.012 [-0.035, +0.004] | 0.239 |
| llm_thr_ru | 0.674 | -0.011 [-0.034, +0.004] | 0.239 |
| llm_l2_ru | 0.676 | -0.009 [-0.028, +0.008] | 0.239 |
| llm_t3_ru | 0.672 | -0.013 [-0.043, +0.011] | 0.241 |
| llm_rh_ru | 0.651 | -0.034 [-0.070, -0.000] | 0.249 |

Lag-1 correlations with missile_day: thr_ru +0.27, all LLM features between -0.07 and +0.03.

Reading: the rule shares reproduce test 2 (+0.02..0.03 AUC); the LLM-refined shares carry nothing. Two readings,
not yet separable: (a) the small lead is in the *volume of threat-flavoured talk*, not in posts the model judges
to be real threats; (b) the per-day LLM share is too noisy at this sample size: ~28 labels/day with p≈0.1 gives a
binomial SE of ≈0.06 on a mean of 0.10 (relative error ~60%), which is enough to drown a +0.03 effect. The full
batch (all 26,848) would cut that error by ~sqrt(5). Decision: do not spend on re-labeling until the company key
question is settled; if (b) is to be tested cheaply, label only the 90 missile days ± 1 in full (~12k posts).

## Test 4 (US-envoy diplomacy: Witkoff / Kushner visits and massive strikes) — run 2026-10-19

Question (Oleksii): do massive strikes cluster around Witkoff/Kushner trips to Moscow and/or Kyiv?
Data: `data/national_daily.csv` — national daily launched missiles / drones (Air Force summaries, `waves`),
`dip` = posts per day in the whole corpus mentioning Віткофф or Кушнер (regex, all channels), `peace` = posts about
peace talks in general (control), `posts` = corpus volume. Window 2026-03-31 … 2026-10-18 (202 days).
Script: `src/test4_diplomacy.py`.

Events, pre-registered before looking at outcomes:
- (a) confirmed in-person visits inside the window: Moscow Sat 2026-09-05 (3-hour Putin meeting), Kyiv Sun 2026-09-06
  (first visit); Putin had announced a 72-hour pause on Kyiv strikes from midnight 09-05. The Jan 22 Moscow visit is
  before the panel starts (needs a `waves` backfill to January). October: phone calls only, no confirmed trip.
  So n = 1 episode — descriptive only.
- (b) "diplomacy episodes": first day of a run with dip ≥ 5 posts and ≥ 3× the trailing-14-day median. Objective,
  recurs 10 times (04-08, 04-24, 06-08, 06-14, 07-05, 08-31, 09-04, 09-23, 10-09, 10-15).
Outcome: national "massive" day = missiles ≥ 20 or drones ≥ 400 (24 of 202 days, base rate 0.12).
Stat: share of episodes followed by a massive day within k days vs. the same share for the episode set circularly
shifted to random dates (5,000 shifts; keeps the autocorrelation of both series).

| within k days after an episode | observed (n=10) | random-date expectation | one-sided p |
|---|---|---|---|
| 1 | **0.40** (04-25, 06-15, 07-06, 10-16) | 0.12 | **0.036** |
| 2 | 0.50 | 0.22 | 0.060 |
| 3 | 0.50 | 0.31 | 0.18 |
| 5 | 0.70 | 0.47 | 0.11 |
| 7 | 0.80 | 0.59 | 0.14 |

Massive rate ON the episode days themselves 0.15 vs 0.12 (no lull on the talk days in the national series).
Linear correlations dip[t−k] × missiles[t] are all ≈ 0 (+0.05 at k=1): the relation, if real, is "spike → next-day
massive strike", not "more talk → more missiles".

Confirmed visit, national series: 09-01…09-07 no missiles, 92–317 drones (the quietest week of the panel; the
announced Kyiv pause is visible nationally too); 09-08, two days after the Kyiv visit, 32 missiles / 166 drones;
09-12/13 the two largest drone nights of the panel (539 / 776). Pattern consistent with "pause during the visit,
heavy strikes right after", but it is one episode.

Out-of-sample day-ahead model (protocol of tests 2/3: expanding logistic, baseline = own lags; 172 test days):

| target | baseline AUC | +dip (count) | +dip_share | +dip_day | +peace (control) | +thr_ru |
|---|---|---|---|---|---|---|
| national massive day (22 positives) | 0.488 | 0.554 (**+0.067** [−0.05, +0.21]) | +0.051 | +0.033 | −0.016 | −0.011 |
| Kharkiv missile day (84 positives) | 0.677 | −0.005 | −0.011 | −0.017 | −0.002 | +0.024 |

Reading:
1. Four of the ten diplomacy spikes were followed the next day by one of the panel's biggest national strikes
   (04-25: 47 missiles/619 drones; 06-15: 70/611; 07-06: 68/351; 10-16: 464 drones after 718 on 10-15). Against a
   12% base rate that is p ≈ 0.04 at lag 1 — but it is the best of five lags tested on ten events, so treat it as a
   lead worth tracking, not a finding. A general "peace talks" mention series carries nothing (control passes): the
   effect, if any, is specific to the envoys, not to diplomacy talk in general.
2. The national massive-day model is near chance on its own lags (AUC 0.49; massive days are not autocorrelated),
   and the envoy count is the only feature that moves it (+0.07 AUC, CI wide, 22 positives). For Kharkiv missile
   days it adds nothing — Kharkiv is hit on ordinary days; the envoy signal, if real, is about nationwide salvos.
3. Mechanism is undetermined by this data: "strike as answer to talks", "talks scheduled when a strike is expected",
   and "both driven by a third thing (e.g. a US deadline)" all fit. Next step that would separate them: hour-level
   timing (did the spike in mentions precede the launch, or did the launch produce the mentions?) — the corpus has
   post timestamps, the waves table has launch windows; and a January backfill to include the 01-22 visit.
Decision: keep `dip` (envoy mentions, lag 1) as a candidate feature in the national model; re-run when the next
confirmed visit happens; no claim yet.

### Test 4b — reading the posts behind the spikes (hour level), same day

What the ten "envoy spikes" actually were, from the posts (Kyiv time):
- **04-08/09, 04-24/25, 06-08 — Iran, not Ukraine.** The April posts are CNN/Axios: "Trump sends Witkoff and Kushner
  to Pakistan for talks with Iran" (04-24 18:57–23:48). The Islamabad talks dominate the envoy series in April–June.
  Split: `dip_ua` (no Iran/Pakistan context) vs `dip_ir`.
- **06-14 — Putin–Trump phone call** (Trump's 80th birthday; Ushakov readout 20:10: "agreed that Witkoff and Kushner
  will come to Russia again soon"). The combined strike of the night of 06-15 (70 missiles / 611 drones) is counted
  by the Air Force "from 18:00 on 06-14" — the Shahed wave starts in the same hour as the call, missiles follow at
  night. A strike of that size is prepared over days: it was not an *answer* to the call.
- **07-04/05 — Putin–Trump call** (1.5 h, 07-04 evening; posts 07-05 00:15–02:00). Massive strike the next night
  (from 18:00 07-05: 68 / 351) — ~24 h after the call.
- **09-04…09-08 — the visit itself.** Spike builds 09-04 (21 posts), peaks 09-05/06 (40–45). No missiles 09-01…09-07,
  then 32 missiles on the night of 09-08 (the day of the next Putin–Trump call, readout 19:20) and the two biggest
  drone nights 09-12/13.
- **10-09/10, 10-13, 10-15 — Putin–Trump call (10-09) and the sanctions/Starlink crisis** talk; 39 missiles 10-14,
  718 drones 10-15.

Cleaner event: Kremlin (Ushakov) readouts of **Putin–Trump phone calls** — 04-29, 06-14, 07-04, 09-08, 10-09 (n=5).
What followed: 04-29 → nothing (drone data missing 04-30, 05-03…05-05); 06-14 → 70/611 that night; 07-04 → 68/351
next night; 09-08 → 539/776 drones on +4/+5; 10-09 → 39 missiles +5, 718 drones +6. Within 2 days 2/5 (random 0.22),
within 5 days 4/5 (random 0.46, p≈0.2). Ukraine-context envoy spikes (n=5): within 5 days 4/5, p≈0.10.
Iran-context spikes (n=3): nothing beyond chance.

Reading: at hour level the sequence is not "talk → strike as reaction" — on 06-14 the drones were already in the air
when the readout came out, and a 50–70-missile salvo is assembled over days. The pattern that survives is the one
reported for 2025 (the 07-03-2025 call followed by the then-record 550-drone night on Kyiv): **large pre-planned
salvos land within 0–2 days of a Putin–Trump contact**, and the two biggest drone nights of this panel came 4–5 days
after the Kyiv visit and the 09-08 call. With five calls it is a pattern to watch, not a result; the call dates are
the feature to keep (`call` column), not the mention counts, because they are exact and not contaminated by Iran.

### January backfill (waves now start 2026-01-01)

Air Force night summaries for 01-01…03-29 were paged from t.me/s/kpszsu (86 nights; day summaries were not posted in
that format then; parser fixed for the "205-ма ударними БпЛА" wording). Loaded into `waves`. Around the 01-22 Moscow
visit (Witkoff + Kushner, Thursday): 01-20 33 missiles / 339 drones (two days before), 01-21…01-23 quiet (≈95 drones
a night, no missiles), 01-24 19 missiles / 375 drones (two days after), then 02-03 67 / 450. Same shape as September:
quiet on the visit days, a combined strike within two days on either side. Two visits, same shape — still n=2.
Jan–Mar massive nights for reference: 01-09, 01-20, 02-03, 02-07, 02-12, 02-17, 02-22, 02-26, 03-07, 03-14, 03-24, 03-29
(roughly weekly — the base rate is ~0.13/day there too). No corpus before 03-31, so no mention features for this stretch.

## Test 3b (LLM labels completed by an open embedding model) — run 2026-10-19 evening, 100% coverage

The missing 21k labels were filled for free: `intfloat/multilingual-e5-small` embeddings of all 27,053 rule-flagged
panel posts (GitHub Actions CPU, 53 min), logistic classifier trained on the 5,633 Haiku-labeled posts, predictions
out-of-fold for labeled posts and fitted for the rest (`src/embed_labels.py`, table `message_labels_pred`,
`data/llm_daily_full.csv`). Agreement with Haiku (5-fold OOF): threat≥2 AUC 0.88 / F1 0.76; l2 0.88 / 0.75;
threat≥3 0.87 / 0.67; rhetoric 0.91 / 0.73; operational 0.97 / 0.92. Daily features = expected counts (sum of p).

| signal (lag 1 + 3-day mean) | AUC | Δ vs baseline 0.685 [CI] | lag-1 corr with missile_day |
|---|---|---|---|
| thr_ru (rule share, reference) | 0.708 | +0.023 [-0.016, +0.070] | +0.27 |
| l2_ru (rule) | 0.714 | +0.030 [-0.004, +0.066] | — |
| p2_ru (precision of the rule) | 0.683 | -0.002 | +0.24 |
| llm_thr_ru | 0.704 | +0.019 [-0.027, +0.077] | **+0.31** |
| llm_l2_ru | 0.702 | +0.017 [-0.029, +0.075] | **+0.32** |
| llm_t3_ru | 0.700 | +0.015 [-0.028, +0.069] | +0.31 |
| llm_rh_ru | 0.686 | +0.001 | — |

Reading: the 20%-sample null in Test 3 was sampling noise (reading (b) was right): at full coverage the LLM-refined
shares carry the same small lead as the rule shares (+0.015…+0.019 AUC, CI spanning zero), with slightly higher raw
lag-1 correlations (+0.31 vs +0.27). The refinement does not add predictive power beyond the regex share — the lead
lives in the *volume of threat-flavoured talk the day before*, not in which posts a model deems real threats. The
"rhetoric" (non-operational) share carries nothing. Decision: the rule share `thr_ru`/`l2_ru` stays the working
feature; LLM labeling is not worth paying for at this horizon. Embeddings stay (cached) for the contradiction-candidate
and repost-dedup work.
