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
