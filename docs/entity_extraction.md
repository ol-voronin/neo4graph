# Entity extraction for the anti-corruption layer

Goal: turn posts from NABU/SAP/HACC-adjacent sources into graph-ready records: cases, public persons in their
case role, organisations, assets named in case materials, amounts and checkable claims. Output feeds Postgres first
(`entity_extractions`), Neo4j later, once entity resolution works on a test set of cases.

## Scope and data rules (fixed, not tunable by prompt)

Allowed identifiers (public role or public document): full name + position/office, case number or case nickname,
agency (NABU/SAP/HACC/NAZK/SBI...), company name and EDRPOU, NAZK declaration id, Prozorro tender id, court decision
number, asset descriptions as written in the public case material (make/model/year of a vehicle, type and location
of real estate at city level, cash amounts), sanctions list entries.

Never extracted or stored, even if present in the text: personal tax ids (RNOKPP), passport or ID numbers, phone
numbers, e-mail addresses, home addresses (street/house level), vehicle plates of private persons, bank account or card
numbers, data about minors or relatives who hold no public office. The model sets `pii_dropped: true` when it skipped
any of these. People appear only when they are public officials, candidates, heads/owners of companies in the case,
or already named as suspects/defendants in the official source; bystanders, victims and witnesses who are private
persons are not extracted (`role: private_skipped` with no name).

Vehicles: a vehicle appearing in two cases is a legitimate graph link, but make/model/year is a weak identifier, so
links by it are `match: probable`; a plate or VIN quoted in an official public document is `match: strong`.

## Output schema (strict JSON)

```json
{
  "is_case_related": true,
  "cases": [{"ref": "string|null", "nickname": "string|null", "agency": "NABU|SAP|HACC|NAZK|SBI|SBU|court|other|null",
             "stage": "investigation|suspicion|indictment|trial|verdict|appeal|closed|other|null", "summary": "<=200 chars"}],
  "persons": [{"name": "as written", "name_norm": "Прізвище Ім'я (nominative, Ukrainian)", "role": "suspect|defendant|convicted|acquitted|official_commenting|investigator|prosecutor|judge|lawyer|journalist|politician_mentioned|owner|other|private_skipped",
               "position": "string|null", "org": "string|null", "public_figure": true}],
  "orgs": [{"name": "string", "edrpou": "string|null", "kind": "company|state_body|court|ngo|party|media|other", "role": "string|null"}],
  "assets": [{"type": "vehicle|real_estate|cash|securities|land|other", "description": "as written, no plates for private persons", "plate_or_vin": "string|null", "value": null, "currency": null, "linked_person": "name_norm|null"}],
  "amounts": [{"value": 0, "currency": "UAH|USD|EUR|other", "what": "bribe|damage|bail|assets_seized|contract|other"}],
  "claims": [{"text": "<=200 chars", "kind": "allegation|established_fact|denial|court_finding", "by": "who states it"}],
  "pii_dropped": false
}
```

## System prompt

```
Ти витягуєш структуровані сутності з публічних повідомлень про антикорупційні справи в Україні (НАБУ, САП, ВАКС, НАЗК, ДБР, суди, журналістські розслідування). Поверни ТІЛЬКИ валідний JSON за схемою нижче, без пояснень.

Правила:
1. Витягуй лише те, що прямо написано в тексті. Нічого не додавай зі своїх знань. Якщо поля немає в тексті — null або порожній список.
2. Люди: лише публічні посадовці, кандидати, судді, прокурори, адвокати у справі, власники/керівники компаній у справі, або особи, яких джерело вже прямо називає підозрюваними/обвинуваченими. Приватних осіб (потерпілі, свідки, родичі без посади) не називай: додай запис з role "private_skipped" без імені.
3. Ніколи не витягуй: РНОКПП/ІПН, номери паспортів, телефони, e-mail, домашні адреси, номери авто приватних осіб, банківські реквізити, дані про дітей. Якщо щось із цього було в тексті — пропусти і постав pii_dropped: true.
4. role описує роль саме в цій справі за текстом. "Підозра" ≠ "обвинувачення" ≠ "вирок": не підвищуй статус.
5. name_norm: Прізвище Ім'я у називному відмінку українською, без посади.
6. Активи — як написано (марка, модель, рік, тип нерухомості, місто). Номерний знак — лише якщо його наводить офіційне джерело щодо публічної особи.
7. claims: короткі перевірювані твердження з позначкою, хто їх робить (слідство, суд, захист, журналіст).
8. is_case_related: false, якщо повідомлення не про конкретну справу/розслідування (загальні новини, анонси, статистика) — тоді всі списки порожні.
```
