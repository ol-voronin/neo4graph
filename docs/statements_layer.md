# Statements, actions and events layer

Three things the project tracks, in one extraction pass per post:

1. **Statements** — what a side *says*: threats, promises, forecasts, claims of fact, demands, denials, announced
   actions. Each statement that implies something checkable gets a `predicted_event` and a time window. Later a
   resolver marks it true / false / partial / unresolvable. The share of true statements per actor (and per type)
   is the actor's **track record**, which feeds the prediction model as a credibility weight: a threat from an actor
   whose threats come true 60% of the time is a different feature from one at 5%.
2. **Actions** — what a side *does*: sanctions packages, aid and weapons deliveries, mobilisation, negotiations,
   strikes on Russia, legal acts. Stored as events with `event_type` and actor.
3. **Strike events by region** — ground truth for all of Ukraine, per oblast and day: missiles, drones, guided
   bombs, casualties, from the Air Force, regional administrations and news. This replaces the Kharkiv-only
   series with a panel (25 oblasts × days), which is what makes region-level prediction testable.

Actors are tagged by side and, for the US, by camp: `US_admin` (White House, State, Pentagon), `US_right`
(Republican politicians and aligned figures), `US_left` (Democrats and aligned), `US_congress_bipartisan`. The
extractor assigns the camp from the speaker; unknown speakers get `US_other`. Russian: `RU_official`,
`RU_milblogger`, `RU_media`. Ukrainian: `UA_official`, `UA_media`. Plus `EU`, `NATO`, `other`.

Regions use the alert-map slugs: cherkaska, chernihivska, chernivetska, dnipropetrovska, donetska,
ivano-frankivska, kharkivska, khersonska, khmelnytska, kirovohradska, kyiv-city, kyivska, luhanska, lvivska,
mykolaivska, odeska, poltavska, rivnenska, sumska, ternopilska, vinnytska, volynska, zakarpatska, zaporizka,
zhytomyrska; `ukraine` for nationwide; `russia:<region>` for strikes on Russia.

## Output schema (strict JSON)

```json
{
  "statements": [{
    "actor_side": "RU_official|RU_milblogger|RU_media|UA_official|UA_media|US_admin|US_right|US_left|US_congress_bipartisan|US_other|EU|NATO|other",
    "actor_name": "speaker or institution as written, null if unclear",
    "stype": "threat|promise|forecast|claim_fact|demand|denial|announce_action",
    "quote": "<=200 chars, as written",
    "predicted_event": "one sentence: what must happen for this to be true, or null if nothing checkable",
    "target_region": "region slug|ukraine|russia|null",
    "horizon_days": 7,
    "resolvable": true
  }],
  "events": [{
    "event_type": "strike|sanction|aid|weapons_delivery|mobilisation|negotiation|attack_on_russia|legal_act|other",
    "event_date": "YYYY-MM-DD (Kyiv) or null",
    "region": "region slug|ukraine|russia:<name>|null",
    "actor": "who did it",
    "missiles": 0, "drones": 0, "guided_bombs": 0, "killed": 0, "injured": 0,
    "details": "<=150 chars",
    "confidence": 0.0
  }]
}
```

## System prompt

```
Ти витягуєш із публічного повідомлення (1) заяви сторін і (2) події. Поверни ТІЛЬКИ валідний JSON за схемою.

Заяви: загроза, обіцянка, прогноз, твердження про факт, вимога, спростування, оголошена дія. Для кожної вкажи, хто говорить (actor_name) і його табір (actor_side). Для США: адміністрація (Білий дім, Держдеп, Пентагон) = US_admin; республіканці та їхні союзники = US_right; демократи та їхні союзники = US_left; спільні двопартійні заяви Конгресу = US_congress_bipartisan; інакше US_other. predicted_event — одне речення, що саме має статися, щоб заяву можна було визнати правдивою; якщо заява нічого перевірюваного не обіцяє — null і resolvable=false. horizon_days — у які строки це мало б статися (із тексту; якщо строку немає — 30). target_region — область або "ukraine"/"russia", якщо заява стосується конкретної території.

Події: лише факти, які повідомлення подає як такі, що сталися: удари (ракети/дрони/КАБ по області, загиблі/поранені), санкції, допомога, поставки зброї, мобілізація, переговори, атаки на Росію, правові акти. Для ударів по Україні region — slug області з переліку; кількості — як у тексті, інакше 0. confidence: 1.0 офіційне джерело про свою територію; 0.7 новина з посиланням; 0.4 блогер/без джерела.

Не додавай нічого зі своїх знань. Не оцінюй правдивість. Повідомлення без заяв і подій → порожні списки.
```

## Resolution

A statement with `resolvable=true` is checked after `ts + horizon_days`: the resolver collects posts from
official and news sources in the window (plus 3 days), matching keywords from `predicted_event` and the region,
and asks the model: happened / did not happen / partially / cannot tell from this evidence. Evidence message ids are
stored with the verdict. Verdicts are reversible (a later pass with more evidence can overwrite). Track record =
true / (true + false), with partial counted as 0.5, over resolved statements only; `n_resolved` is shown next to the
percentage because 3/4 is not 75%.
The resolver ranks evidence by keyword stems matched and adds structured ground truth; `unresolvable` verdicts are
retried later (`resolve_statements.py --retry`). Per-person / group / country trust scores with shrinkage and
intervals replaced the plain percentage in the UI: see docs/factcheck.md.

## What the layer gives the model

- Per actor and type: track record and its Wilson interval → credibility weight on live threats.
- Per region and day: strike intensity panel → region-level targets and "rhetoric mentions region R → strike in R".
- Say-vs-do gaps: announced actions without matching events, and events never announced.
