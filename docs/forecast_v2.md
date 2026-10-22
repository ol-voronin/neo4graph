# Oblast model v2: honest negatives + target-class signals (2026-10-22)

Models `ua_oblast_massive2_v1` (candidate) and `ua_oblast_massive2_base_v1` (baseline), frozen 2026-10-22, next to the
frozen v1 pair (`ua_oblast_massive_v1`, `_base_v1`), which keeps running unchanged. Same target event and issue rule:
oblast day D with ≥ 7 missiles or ≥ 20 strike drones (FPV excluded), forecast written before 24:00 on D-1 from
features available at 15:00 on D-1.

## Why

v1 labels a day negative only when there is a complete OVA summary (Kharkiv only) or when the LLM events extractor read
≥ 100 posts of that day. Outside Kharkiv almost every known day was a day when strikes were reported, so known negatives
were scarce, the pooled base rate was Kharkiv's (~0.9), and the live v1 gave nearly every oblast 0.98–1.00 on
2026-10-23 (Kyiv: 0.999). That number was an artifact of the labels, not of the situation. The extractor's reading
volume also says nothing about a given oblast: reading 100 posts of a day does not mean reading that oblast's news.

## Label `ob_massive2` (`features.label_oblast2`)

- **Positive:** same as v1, from OVA summaries and LLM-extracted strike events (`oblast_daily_gt`).
- **Negative:** a complete OVA summary, **or** no oblast-level air-raid alert in the oblast that Kyiv day while the alert
  feed was alive (≥ 3 oblasts had an alert that day; a day with no alert rows at all is a data gap, not a quiet day).
  Not in occupied oblasts (Luhansk, Crimea), where alerts do not sound.
- **Unknown** otherwise (an alert day with no strike report stays unknown, not zero).

Check on 2026-03-31..10-21: outside Luhansk, no positive day lacked an alert (Luhansk: 8, hence the exclusion).

Known labels per oblast, 2026-03-31..10-21 (positives / negatives v1 → v2):

| oblast | positives | negatives v1 | negatives v2 |
|---|---|---|---|
| kyiv-city | 5 | 0 | 23 |
| kyivska | 2 | 0 | 10 |
| kharkivska | 185 | 20 | 20 |
| zaporizka | 35 | 0 | 0 |
| odeska | 3 | 0 | 5 |
| zakarpatska | 0 | 0 | 179 |
| luhanska | 8 | 0 | 0 (excluded) |

(The v1 column counts complete-summary negatives; v1 also made negatives from extractor volume, which v2 drops.)
Alert-free days are common in the west (Zakarpattia 179, Ivano-Frankivsk 159) and rare at the front (Dnipro,
Zaporizhzhia, Sumy 0–3), so those oblasts still have few known days.

## Signals (candidate only; same 24 h window ending 15:00 on D-1)

- `l2_ru`, `thr_geo_ru` as in v1;
- `tc_energy_ru`, `tc_dc_ru`, `tc_rail_ru`, `tc_vpk_ru`: share of RU panel posts that are threats (rule `r_threat`) and
  name a target class — energy, data centers, rail, defence industry (`features.TC_RE`; regexes, no LLM);
- `tcg_ru`: share of RU panel threat posts that name THIS oblast and an infrastructure class (energy | data centers |
  rail) (`features.INFRA_RE`, geo matching via `geo_regions()`).

## Backtest (expanding window, live code path; 2026-05-30..10-22)

| model | known oblast-days | positives | AUC | within-oblast AUC | Brier | Δ AUC vs base |
|---|---|---|---|---|---|---|
| ua_oblast_massive2_v1 | 915 | 187 | 0.979 | 0.59 | 0.028 | −0.000 (CI −0.008…+0.011); shuffled-date control p 0.36 (10 runs) |
| ua_oblast_massive2_base_v1 | 915 | 187 | 0.979 | 0.47 | 0.023 | — |
| ua_oblast_massive_v1 | 431 | 187 | 0.866 | 0.56 | 0.143 | +0.009 (CI −0.069…+0.089) |
| ua_oblast_massive_base_v1 | 431 | 187 | 0.857 | 0.51 | 0.140 | — |

Calibration (mean forecast → observed share of event days):

- v2 candidate: 0–20 %: 0.01 → 0.02 (724 days); 80–100 %: 0.97 → 0.95 (163).
- v1 candidate: 0–20 %: 0.03 → 0.02 (169); 80–100 %: 0.94 → 0.76 (225) — overconfident.

Kyiv (city): v2 mean forecast 0.23 vs observed 0.33 on 21 known days (7 events); v1 0.65 vs 0.47 on 15.

## What this means

- v2's probabilities are calibrated where v1's high band was overconfident, and the Kyiv artifact is gone.
- The pooled AUC (0.98) mostly measures *which oblast* (Kharkiv/Zaporizhzhia vs the west), not *which day*. Within an
  oblast, discrimination is weak (0.47–0.59).
- The rhetoric and target-class signals add nothing detectable over the baseline in the backtest (Δ AUC 0.000, shuffled
  control p 0.36). The prospective test decides; the backtest is orientation only.

## Caveats

- Alert data: the volunteer feed in the database ends 2026-10-17, the official one 2026-09-07. Days after the last
  alert load have no honest negatives until the alerts are loaded again; live forecasts then lean on older labels.
- Positives outside Kharkiv come only from LLM-extracted strike events; an alert day without a report stays unknown, so
  known days in quiet oblasts are mostly negatives and in front oblasts mostly positives.
- The target-class regexes are coarse (`завод` matches any plant); a class signal is a share of threat posts, not a
  statement about a specific object.
- Small samples per oblast (Kyiv 21 known days, Odesa 3, Chernihiv 2).
