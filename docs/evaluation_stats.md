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
