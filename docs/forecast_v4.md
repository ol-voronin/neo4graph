# Oblast model v4: explosions in the oblast tomorrow (pre-registered 2026-10-23)

Why: the massed-strike target (>= 7 missiles or >= 20 strike drones per oblast) is almost never true in the rear, so the
v1-v3 maps say ~0% for Lviv or Volyn on a morning when the monitors report explosions there (23.10.2026). People ask
"will there be explosions here", which v4 answers.

- Target `ob_expl` (src/explosions.py, table explosion_daily): >= 1 report of explosions naming the oblast on Kyiv day D in
  the monitoring channels povitryanatrivogaaa, eRadarrua, war_monitor, vanek_nikolaev. Known negative: no report while
  the feed was alive (>= 30 monitor posts that day); Luhansk and Crimea get no negatives. The label is mostly air threats
  (missiles, drones, KAB, air defence); frontline artillery is under-reported by these channels.
- Models `ua_oblast_expl_base_v1` (region, shrunk base rate, own D-2 and 7-day mean, ACLED published-week rates) and
  `ua_oblast_expl_v1` (+ the v3 rhetoric signals: RU panel threat share, target-class shares, threats naming the oblast).
  Same protocol as v3: issued after 15:00 on D-1, frozen; Δ AUC candidate - baseline with a block-bootstrap CI and the
  shuffled-date control (`nowcast.py backtest --model ua_oblast_expl_v1 --controls 50`).
- Decision rule: rhetoric adds a signal only if Δ AUC > 0 with the 95% CI above 0 and shuffled-control p < 0.05.
- Specs are frozen as of this file; a change means a new file and name.

## Result (backtest 2026-10-24)
`nowcast.py backtest --model ua_oblast_expl_v1 --controls 50`, test days from 2026-05-30 (the frozen spec reads daily rhetoric
from the live panel only), 3 387 oblast-days, 1 322 explosion days: AUC 0.847 with rhetoric vs 0.866 baseline, Brier 0.154 vs
0.143; Δ AUC −0.018, 95% CI [−0.035, −0.003]; shuffled control p = 0.96. Within-region AUC 0.613 vs 0.595 (not the
pre-registered metric). **Decision rule not met: rhetoric makes the explosion forecast slightly worse.** Using the 2022–2026
history for the daily features needs a new pre-registered version.
