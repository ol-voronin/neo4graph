# rhetoric-strikes

Exploratory analytical project: does a shift in rhetoric (Telegram, official statements, media,
anti-corruption news as amplified narratives) precede massed missile/drone strikes on Ukrainian cities?

**Status:** research prototype. This is NOT an early-warning system and does not replace official air-raid alerts.

## Hypothesis (fixed before analysis)
A new, coordinated threatening narrative raises the probability of a massed strike in the next 1-5 days
relative to the base rate. Success = a stable lift over baseline on out-of-time test data.

## "Massed strike" definition (set by project owner)
Per single city: **7-10 missiles OR 20+ drones**. Main analysis uses 7 missiles / 20 drones;
7..10 is used for sensitivity analysis. Stored in the `config` table (`massive_threshold`).

## Layout
- `db/schema.sql` - Postgres schema (Neon, PG17, pgvector)
- `db/schema.cypher` - optional Neo4j graph schema (phase 5, only if signal is found)
- `docs/labeling_prompt.md` - LLM labeling prompt, output schema, QA procedure
- `src/` - collectors and analysis (to be added)
- `data/` - local raw data (git-ignored)

## Phases
0. Frame hypothesis and thresholds
1. Ground truth: alerts and strike waves into `alerts`, `waves`, `wave_regions`
2. Source collection (Telegram via Telethon, official sites, ISW, calendar of events)
3. LLM labeling + manual QA (target Cohen's kappa >= 0.6)
4. Lead-lag analysis vs baseline and shuffled controls; strict time-based train/test split
5. Graph layer (Neo4j) if a signal exists
6. Model + dashboard
7. Operations and drift monitoring

## Setup
```
cp .env.example .env   # fill in values, never commit .env
pip install -r requirements.txt
```

## Ethics
- Public sources only.
- Anti-corruption cases are stored as *status* (suspicion/indictment/verdict), never as guilt.
- Source reliability is scored from history, not assumed.
