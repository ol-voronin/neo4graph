# Evaluation of feature extractors and target definition (2026-10-18)

Gold sample: table `gold_sample` (106 posts, labels by Claude, threat 0-3 as in labeling_prompt v2). Strata: A = 40 posts
already labeled by Gemini v2; B = random posts per rule level (0 / 1 / >=2). Rule level = `rule_labels.rule_threat_level`.

| Result | Value | 95% CI (Wilson) |
|---|---|---|
| Gemini v2 vs gold, stratum A (agreement) | 40/40 | 91-100% (but only 1 positive in A) |
| Rule level >=2: precision (gold>=1 among flagged) | 2/26 | 2-24% |
| Rule level >=1: precision | 2/53 | 1-13% |
| Rule level 0: gold>=1 found | 0/53 | upper bound ~6-7% (rule of three) |
| Prevalence of gold threat>=1 (stratified, Jeffreys) | 0.7% | 0.4-4.9% |

Reading: explicit forward-looking threat posts are ~1% of the feed, so any keyword filter has low PPV (Bayes: PPV = sens*prev /
(sens*prev + (1-spec)*(1-prev))). Rules are a prefilter and a rate feature, not a labeler. Recall cannot be estimated from this
sample (2 positives); needs a purposive positive-rich sample (Medvedev, Zelensky, MFA statements) - next step.

Caveats: single annotator (no kappa yet); sample drawn from the newest ~2.7k posts only; Gemini v2 on B strata not run.

## Target definition (Kharkiv, 22 days from governor summaries, 2026-09-25..10-16)
Oblast-level "city attacked AND (missiles>=7 OR drones>=20)" = 15/22 = 68% (CI 47-84%): almost every day is "massed", so it does
not discriminate. Missiles>=7 alone = 3/22 (14%). Use a continuous intensity index and quantile thresholds (top quartile) and
rank metrics (AUC) instead of a binary 7/20 label until per-city counts are available.

## Power
Detecting a rise in P(massed) from 0.4 to 0.6 after a signal needs ~97 days per group (alpha .05, power .8); 0.4 -> 0.7 needs ~42.
Current ground truth: 22 days Kharkiv, 18 days nationwide waves (all massed). It is the bottleneck, not the number of posts.

## Update 2026-10-18 (recall sample + siren proxy)
- Stratum C (32 purposive posts from Medvedev / Zelensky / MFA / Kremlin / Soloviev / RIA etc. with threat-like vocabulary): 4 positives
  (Medvedev x2 explicit threats = 2, Zelensky warning that Russian airspace becomes unsafe = 2, US arms-cutoff threat = 1, borderline).
- Rules recall on all 6 gold positives (A+B+C): 5/6 = 83% (Wilson 44-97%); purposive sampling makes this optimistic.
  Missed: Zelensky's airspace warning (no keywords). Conclusion: rules are acceptable as a cheap prefilter only if tuned on a bigger positive set.
- Siren proxy for Kharkiv: oblast `air_raid` alerts per day avg 19 (350 min) over the 22 days with governor summaries; Spearman between
  intensity (10*missiles + drones) and alert count = -0.03, vs alert minutes = -0.10 (n=22, rough 95% band about +-0.43). Alerts run almost
  every day, so they do NOT measure strike intensity. Do not use alert counts as the target.
- Public history of daily missile/drone launches (2022+) was not found as a GitHub dataset reachable from here; t.me/s paging only reaches
  ~17-30 days for busy channels. Candidates: Kaggle "Massive Missile Attacks on Ukraine", ISW/Air Force daily posts, Wikipedia timeline.

## Recall of rules (purposive positive-rich samples C, 68 posts; total gold positives 11 across all strata)
Positives by rule level: level 0 -> 3, level 1 -> 1, level 2 -> 5, level 3 -> 2 (of 11).
Recall of rule>=1 = 8/11 = 73% (CI 43-90%); recall of rule>=2 = 7/11 = 64% (CI 35-85%). A keyword prefilter at level>=1 would lose ~27%
of real threat posts (e.g. Zelensky's "we will respond" and the Houthi warning got level 0). Decision: do NOT use rules as an LLM prefilter
for rhetoric channels; only skip by source type (alert/monitor feeds, short alert templates). Purposive samples are not random, so recall
is optimistic/pessimistic in unknown direction; 11 positives is too few for tight intervals. Note: 32 rows of stratum `C_purposive`
were found in the table that were not written in this session (same annotator tag `claude`); kept, but provenance unverified.
Ground truth history: t.me/s paging resets the connection after ~400 posts of busy channels (kpszsu ~200 posts/day), so deep history
is not reachable from here; ground truth grows through the 30-min cron instead.

## Gemini v2 vs gold vs rules (156 labeled gold posts, 11 gold positives = threat>=1; 2026-10-18)
| Metric (threat>=1) | Gemini v2 | Rules (level>=1) |
|---|---|---|
| Recall | 8/11 = 73% [43-90%] | 9/11 = 82% [52-95%] |
| Precision | 8/15 = 53% [30-75%] | 9/100 = 9% [5-16%] |
| Specificity | 138/145 = 95% [90-98%] | 54/145 = 37% [30-45%] |
Gemini: Cohen's kappa 0.58 (binary), exact agreement on 0-3 scale 140/156 = 90% [84-94%].
Errors: Gemini inflates levels for rhetoric about third theaters/pressure (Houthi warning -> 3, Iran strike talk -> 2, US arms-cutoff threat -> 2,
Medvedev/Zelensky warnings 3 vs gold 2) and misses mild items (MID reply on NATO nuclear drills, Rybar speculation); several of these are borderline
in the single-annotator gold, so true disagreement is likely smaller. Rules are not usable as a labeler (91 false positives) but have decent recall.
Decision: keep Gemini as the labeler; use a coarse binary (threat>=1) and ordinal sum, not the exact 0-3 level, in the lead-lag analysis.
Scheduled `label` failures on 2026-10-18 were (a) Gemini free-tier 429 quota and (b) Neon dropping an idle SSL connection during retries;
fixed: stop cleanly on repeated 429, reconnect, cron every 3h, limit 100.
