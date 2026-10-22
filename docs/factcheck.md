# Fact-checking layer

Goal: for every collected message, extract **checkable claims**, check them against independent evidence,
and keep a per-source accuracy history. The accuracy history feeds source reliability (`sources.reliability`)
and the "noise" weights in the rhetoric model.

## Principles
1. **A claim is not a fact.** Official statements from any side are stored as *claims by an actor*.
2. **Evidence must be independent of the claimant** where possible (alerts, ACLED, NASA FIRMS fire detections,
   satellite imagery, geolocated footage, other-side admissions, third-country reporting).
3. **Verdicts are explicit and conservative.** Most military claims are `unverifiable` or `no_evidence_found`.
   `no_evidence_found` is not `contradicted`.
4. **Never rate people, only claims.** Anti-corruption items keep their procedural status (suspicion / indictment /
   verdict); we check "was a suspicion announced on date D by agency A", never guilt.
5. Every check stores the method, evidence and a check_version so results can be reproduced and revised.

## Tables (see db/schema.sql)
- `claims(message_id, claim_text, claim_type, actor, place, claim_date, quantity, quantity_unit, checkable)`
- `claim_checks(claim_id, verdict, confidence, method, evidence jsonb, evidence_urls[], note, check_version)`
- `source_accuracy(source_id, period, claims_checked, supported, contradicted, no_evidence)`

Verdicts: `supported`, `partially_supported`, `contradicted`, `no_evidence_found`, `unverifiable`, `outdated`.

## Automated checks (first set)
| id | claim | evidence | strength |
|----|-------|----------|----------|
| C1 | "struck target in city X on date D" (RU MoD, milbloggers) | alerts in X's oblast on D; kpszsu hit locations; ACLED; FIRMS | weak to medium |
| C2 | "N drones/missiles launched / shot down" (UA Air Force) | consistency with its own later revisions; independent counts where published | internal only |
| C3 | "air defence shot down N Ukrainian drones" (RU MoD) | no independent source in this project: stored as `unverifiable`, tracked as a rhetoric feature | none |
| C4 | "attack on <region> / drones heading to <city>" (monitoring feeds) | alerts + later official summaries | medium |
| C5 | "suspicion announced / case opened" (anti-corruption) | agency site, court register | strong |
| C6 | scheduled-event claims ("talks on date D") | official calendars, wire reports | strong |

Also tracked: **claim timing**. A claim posted after the event it describes is `post_hoc` and gets zero weight as a
predictor, but still counts for source accuracy.

## What needs an LLM
Claim extraction (`claims`) and fuzzy matching of places/dates. This runs with `ANTHROPIC_API_KEY` (see `.env.example`)
using the claim-extraction prompt in `docs/labeling_prompt.md` (mode C). Rule-based checks (C1, C4, C5) then run in SQL/Python.

## Known biases to control
- Each side reports its own successes favourably; use neutral wording ("claimed by", "reported by").
- Official Ukrainian figures are preliminary and revised; keep versions.
- Telegram channels delete and edit posts; store `ts_collected` and the hash at collection time.

## Levels: Країна · Групи · Людина (migration 019)

The fact-check view (`?view=factcheck`) opens on trust levels; `?view=factcheck&person=<actor id>` is a person page.
API: `/api/factcheck?level=country|group[&id=…]`, `?level=person&id=ru:lavrov`, `?level=find&name=<any spelling>`
(the graph node card and chat use `find` for the «Профіль» button). CDN cache 600 s, like the rest of `/api/factcheck`.

**Person identity.** `actors` (canonical id `ru:lavrov`, `ua:zelensky`, `us:trump`, `ru:mid`, …, Ukrainian name, role,
group, the channels that speak as the actor) and `actor_aliases` (case-insensitive regexes over the speaker name, any
script: `(лавров|lavrov)`, `(зеленськ|зеленск|zelensk)`). `actor_of(side, name, source)` resolves a speaker: longest
alias first; no name → the channel's own actor if the side agrees (`tg:MID_Russia` → `ru:mid`, `tg:medvedev_telegram`
→ `ru:medvedev`); a person-like name without an alias → `<country>:~<surname>` so one person still aggregates; else NULL.
A trigger fills `statements.actor_id` on insert. To merge a new spelling: add an `actor_aliases` row and re-run the two
backfill `UPDATE`s at the end of the migration.

**Country** = the actor's country, else the group's, else `sources.country` (UA / RU / US / EU = EU institutions, NATO
and European governments incl. the UK / other). **Groups** (`actor_groups`, small config table): the actor's group,
else by `actor_side`:

| group | sides |
|---|---|
| `ru_officials` РФ: посадовці й відомства | RU_official |
| `ru_media` РФ: державні медіа | RU_media |
| `ru_milbloggers` РФ: воєнкори | RU_milblogger |
| `ua_officials` Україна: посадовці й відомства | UA_official |
| `ua_media` Україна: медіа | UA_media |
| `us_admin` США: адміністрація | US_admin |
| `us_politics` США: Конгрес і політики | US_right, US_left, US_congress_bipartisan, US_other |
| `eu_nato` ЄС, НАТО, Європа | EU, NATO |
| `other` Інші | other |

`statements_x` is the view the API aggregates (statement + actor + group + country); nothing derived is stored.

**Person page.** Що сказав: all their statements (quote, date, type, source link, verdict). Що зробив: extracted
events naming them (sanctions, deliveries, negotiations, legal acts; strikes excluded), plus the chat's entity dossier
without the graph (roles in anti-corruption posts, cases, court decisions, ЄДР, news posts about them). Де сказав
неправду: statements resolved `false` / `partial` with the resolver's note and the evidence posts / ground-truth rows.
Де суперечив сам собі: `contradictions` for the actor (both quotes, dates, explanation). Рівень довіри: below.

## Рівень довіри (trust score)

Per resolved statement *y* = 1 (true), ½ (partial), 0 (false). `unresolvable` and `pending` do not count; neither do
demands (not true/false by nature). For a set of statements: *n* = number resolved, *S* = Σ*y*.

Hierarchical Beta shrinkage, top-down (`web/api/_trust.js`):

1. all statements: Beta(1, 1) prior → posterior mean *m*₀
2. country *c*: prior Beta(*K·m*₀, *K·*(1−*m*₀)), posterior Beta(*K·m*₀ + *S*_c, *K·*(1−*m*₀) + *n*_c − *S*_c), mean *m*_c
3. group *g* in its country: same with prior mean *m*_c
4. person *p* in their group: same with prior mean *m*_g

*K* = 8 pseudo-statements. Score = posterior mean; interval = 10 % and 90 % Beta quantiles (80 % credible interval).
Countries and groups pool their statements (each statement weighs 1). Below *n* = 5 resolved statements the UI says
«недостатньо даних» and shows the counts instead of a number. Trend = the same posterior recomputed cumulatively at
the end of each month. Example: 2 of 2 true in a group at 0.60 → 0.68 [0.49, 0.86], not 100 %; 80 of 100 → 0.79 [0.73, 0.83].

What the score is and is not: agreement of a speaker's checkable statements with independent evidence, as judged by
the resolver. Not intent (a failed forecast is not a lie), and it inherits the resolver's selection: only statements
that could be checked are scored, and threats "coming true" count as accurate.

## Pipeline and schedule (`.github/workflows/statements.yml`)

All on Haiku 5.5 (`claude()` helper, sends `anthropic-workspace-id`) through the Message Batches API
(`src/statements_batch.py`, same prompts / candidates / write paths as the interactive scripts):

| when (UTC, Neon wake window) | what |
|---|---|
| 00:35, 06:35, 12:35, 18:35 | `statements_batch.py fetch`: write finished extraction and verdict batches |
| 06:35 | `submit-extract --limit 800` (key actors' own channels first, then media that quote named officials) and `submit-resolve --limit 400` (½ due forecasts, ~⅓ claims of fact, rest = retries of `unresolvable`) |
| 12:35 | `find_contradictions.py --provider anthropic --max-actors 25`, canonical actors with new statements since their last pass only |

Sources: `MID_Russia, news_kremlin, medvedev_telegram, mod_russia, V_Zelenskiy_official, GeneralStaffZSU, kpszsu`, the
OVAs, `rss:whitehouse, rss:state_dept, rss:uk_mod, rss:un_news` (every post ≥ 120 chars), and `SolovievLive, rian_ru,
tass_agency, golosameriki`, Ukrainian / Russian independent media and the Western RSS feeds (bbc, guardian, nytimes, ft,
wsj, economist, politico_eu, dw, euronews, aljazeera) when the text looks like a statement or quotes a named official.
(Before this change `tg:kyivindependent` and `tg:bbcukrainian` never matched: source ids are case-sensitive.)

**Cost** (Haiku 5.5 batch prices as used in `label_batch.py`: $0.05 / M input, $0.25 / M output, cache read
$0.005 / M; verify against current pricing): extraction ≈ 0.7 k input + 0.45 k output tokens per post ≈ $0.00015;
resolution ≈ 3–4 k input (evidence) + 0.2 k output ≈ $0.0002. Daily caps 800 + 400 → ≈ $0.12 + $0.08 ≈ **$0.2/day,
~$6/month**; contradictions (interactive, 25 actors × ~4 k tokens) add < $0.05/day. Every submit prints its own
estimate (`--estimate` prints and exits), every fetch prints the actual cost from usage.

## Why so many `unresolvable` (and what changed)

Of the first 751 statements, 337 ended `unresolvable` and only 69 got true/false/partial. Causes in the old resolver:
1. Evidence = the *earliest* 12 posts containing *any* of 12 keywords (7-letter prefixes), so common words
   ("україн", "заявив") filled the list with unrelated posts from the first day of the window.
2. Keywords did not match across scripts and inflections (Харкову / Харькову / Харків).
3. Source ids `tg:kyivindependent`, `tg:bbcukrainian` did not exist, Western RSS feeds were not searched at all.
4. Ground truth: nothing for `ukraine`-wide claims; oblast rows without the `complete` flag or alert counts, so the
   model could not tell "no strikes reported" from "no data"; RU MoD claims added even for Ukrainian statements.
5. The prompt had no rule for absence of evidence, so unfulfilled threats/forecasts defaulted to `unresolvable`.
6. A verdict, once `unresolvable`, was never revisited although news follow-ups arrive later.

Now: evidence is ranked by the number of distinct stems matched (≥ 2 when there are 3+), with Ukrainian/Russian
spelling classes; the statement's own post is excluded and posts from the same channel are marked `same_source`;
structured rows come from `oblast_daily_gt` (with `complete` and alert counts) for any oblast, the sum over oblasts for
`ukraine`, Air Force waves and RU MoD claims; the prompt says that a forecast event absent from complete summaries for
the whole window is `false`, later than the deadline is `partial`; `--retry` re-checks `unresolvable` statements up to
3 times (`statements.n_checks`), ≥ 7 days after the last check, with a window extended by 30 days.
