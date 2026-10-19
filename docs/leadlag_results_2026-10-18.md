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
