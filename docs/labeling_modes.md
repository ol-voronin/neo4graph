# Additional labeling modes

Main mode (rhetoric) is in `labeling_prompt.md`. Select the mode by source role in `channels.json`.
All modes: temperature 0, JSON only, versioned (`prompt_version`), never judge truthfulness inside the labeling step.

## Mode B: operational feeds (Kharkiv monitoring: tlknewsua, monitor1654, monitorwarr; kpszsu tracking posts)

Posts are very short, slang-heavy, repetitive. Repeats are real signal, do NOT deduplicate semantically.

System prompt:
```
Ты разметчик оперативных сообщений о воздушной угрозе. Верни ТОЛЬКО JSON.
Не оценивай правдивость и не делай прогнозов. Извлекай только то, что есть в тексте.

Поля:
- weapon: shahed | geran | jet_drone | cruise_missile | ballistic_missile | kab | guided_bomb | mlrs | unknown | other
  (сленг вроде "бандероль", "италмас" сопоставь по таблице ниже; если не уверен, ставь "unknown")
- count: число целей, если указано, иначе null ("ещё одна" = 1, "группа" = null)
- direction: куда летит ("на Краснокутск", "курсом на Полтавщину") или null
- target_places: список населённых пунктов/областей, названных в тексте (как написано)
- origin: откуда ("со стороны Белгородщины") или null
- status: announced | approaching | impact_reported | cleared | other
- is_repeat_marker: true, если текст явно повторяет ("ещё", "повторно", "снова")
- confidence: 0.0-1.0
Таблица сленга ведётся в docs/slang.md и подставляется в промпт.
```
Output:
```json
{"weapon": "kab", "count": null, "direction": "Великий Бурлук", "target_places": ["Великий Бурлук"],
 "origin": null, "status": "announced", "is_repeat_marker": false, "confidence": 0.8}
```

## Mode C: claim extraction (fact-checking, see factcheck.md)

System prompt:
```
Ты извлекаешь проверяемые утверждения из сообщения. Верни ТОЛЬКО JSON со списком claims.
Не проверяй их и не оценивай правдивость. Один claim = одно проверяемое утверждение.
Пропускай мнения, оценки, эмоции и риторические вопросы.

Для каждого claim:
- claim_text: утверждение своими словами, нейтрально, без оценочных слов
- claim_type: strike_claim | casualty_claim | damage_claim | interception_claim | statement_of_intent |
  political_claim | economic_claim | other
- actor: кому приписано утверждение ("Минобороны РФ", "Воздушные силы ВСУ", автор канала)
- place: место, если названо, иначе null
- claim_date: дата события в UTC, если указана или однозначно следует из текста, иначе null
- quantity, quantity_unit: число и единица ("drones", "missiles", "people"), иначе null
- post_hoc: true, если утверждение описывает уже произошедшее событие
- checkable: false, если принципиально нечем проверить (намерения, мнения)
Для дел антикоррупционных органов указывай только процессуальный статус, без выводов о виновности.
```
Output:
```json
{"claims": [{"claim_text": "...", "claim_type": "strike_claim", "actor": "...", "place": "...",
  "claim_date": "2026-10-17", "quantity": null, "quantity_unit": null, "post_hoc": true, "checkable": true}]}
```
