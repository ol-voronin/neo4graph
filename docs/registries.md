# Open registries: what is searchable and from where

Status: 2026-10-22, branch `registries`. Covers the site's registry search (`GET /api/registry`) and the lake tables it
reads. Every claim about a field below was checked against the actual file or API response on 2026-10-22 unless marked
otherwise.

## What the site can search now

| Search | Example | Source of the match | Notes |
|---|---|---|---|
| ЄДРПОУ (8 digits) | `00034045` | ЄДР legal entities (2.0 M), ProZorro buyers/suppliers, debtors register | exact |
| РНОКПП (10 digits) | `3257209987` | **ProZorro suppliers only** | exact; only ФОП that won a public contract |
| ФОП name | `Шевченко Тарас Григорович` | ЄДР ФОП (7.06 M records) + ProZorro ФОП suppliers | prefix; "Імʼя Прізвище" order also tried |
| Company name | `ТОВ Форестпром`, `Київстар` | ЄДР legal entities (full and short name) | prefix; legal-form words are ignored |
| Address / locality | `Львів Архипенка`, `Віта-Поштова` | ProZorro buyers/suppliers only | street for legal entities; locality only for ФОП |
| Region filter | `&region=Харківська` | ProZorro addresses | entities never seen in ProZorro have no region |

ЄДР dumps carry **no address, no КВЕД and no РНОКПП** (see below), so address and region exist only for entities that
appear in ProZorro contracts. That set grows nightly (see "ProZorro backfill").

## Datasets

| Dataset | URL | Update | Identifiers present | Name | Address / region | Licence | Size |
|---|---|---|---|---|---|---|---|
| ЄДР: юридичні особи (UO) | [data.gov.ua 03cc1239…](https://data.gov.ua/dataset/03cc1239-3988-4451-aa0d-aadb77448714) `UO.zip` | weekly (Minjust) | ЄДРПОУ | yes (+short name, founders, signers, beneficiaries as text) | **no** (not in XML) | CC BY | 328 MB zip, 2.02 M records |
| ЄДР: ФОП (FOP) | same dataset, `FOP.zip` | weekly | **none** (internal `RECORD` number only) | yes | **no** | CC BY | 473 MB zip, 7.06 M records |
| Єдиний реєстр боржників | [data.gov.ua 783b9b50…](https://data.gov.ua/dataset/783b9b50-faba-4cc9-a393-60485e395b1d) | on change (daily) | ЄДРПОУ of legal-entity debtors; **blank for individuals** (9.77 M of 10.6 M rows) | yes | no (only the enforcement office) | CC BY | 743 MB zip, 10.6 M rows |
| ProZorro contracts | [public-api.prozorro.gov.ua/api/2.5/contracts](https://public-api.prozorro.gov.ua/api/2.5/contracts) | real time | buyer ЄДРПОУ; supplier ЄДРПОУ **or РНОКПП of a ФОП** (scheme `UA-EDR`, 10 digits) | yes | yes: region, locality, street, postal code | public by law (Закон «Про публічні закупівлі», ст. 10); no separate licence stated | ~20 k changed contracts/day; full history is millions of contracts |
| spending.gov.ua (Є-Data) transactions | [api.spending.gov.ua/api/v2/api/transactions/](https://api.spending.gov.ua/api/v2/api/transactions/) | daily | payer/recipient ЄДРПОУ; **ФОП recipients masked as `xxxxxxxxxx`** | yes | payer region id only | open data | ~15–40 k transactions/day |
| НАЗК: реєстр декларацій | [public-api.nazk.gov.ua/v2/documents/list](https://public-api.nazk.gov.ua/v2/documents/list) | real time | **no РНОКПП**: the public version hides the declarant's tax number, passport, birth date and registered address (Закон «Про запобігання корупції»; not re-checked live, the API needs a Ukrainian IP) | yes | region of work only | open data | used by the local worker only |
| НАЗК: реєстр корупціонерів | [corruptinfo.nazk.gov.ua/ep/1.0/corrupt/getAllData](https://corruptinfo.nazk.gov.ua/ep/1.0/corrupt/getAllData) | on change | ЄДРПОУ of legal entities only; individuals by name + workplace | yes | legal address of entities | open data | ~80 MB JSON; did not answer from outside Ukraine on 2026-10-22 |

Not used: Opendatabot, YouControl and other commercial re-publishers (no scraping of commercial sites), and the
ProZorro site search (`prozorro.gov.ua/api/search`, unofficial).

### Checked facts

- **ФОП РНОКПП is not in the ЄДР open data.** The official `FOP_schema.xsd` (2026-10-20) lists only `RECORD, NAME, STAN,
  FARMER, REGISTRATION, ESTATE_MANAGER, TERMINATED_INFO, TERMINATION_CANCEL_INFO, EXCHANGE_DATA`. No address, no КВЕД,
  no tax number. The legal-entity XML has no address or КВЕД either.
- `EXCHANGE_DATA` of some ФОП records carries a `START_NUM` under "Реєстр платників єдиного внеску" that often looks
  like a 10-digit tax number. It is a registration number in another register, not a published РНОКПП, so it is
  **deliberately not indexed or shown**.
- spending.gov.ua masks every individual recipient's code (`recipt_edrpou = "xxxxxxxxxx"`, e.g. `ФОП ПОЛІЩУК НАТАЛІЯ ЯНІВНА`),
  so it adds no РНОКПП. Its legal-entity codes are already in ЄДР, so it is not part of the index. Payments stay in
  `public_payments` (src/spending.py) for the graph.
- ProZorro publishes a ФОП supplier as `{"scheme": "UA-EDR", "id": "<10 digits>", "legalName": …}` with a full home
  address. In a sample of 200 recent contracts 84 suppliers had 10-digit codes.
- ProZorro rate limit: 16 parallel fresh connections got HTTP 429; 8 keep-alive connections run at 15–30 contracts/s.

## Privacy

РНОКПП of a ФОП is personal data. The project shows it **only when an official open dataset publishes it as that
person's identifier**, which today means ProZorro supplier records (a ФОП who signed a public contract). Rules applied
in code:

- No РНОКПП is derived, guessed or joined from elsewhere (the ЄСВ number in the ЄДР dump is ignored; spending.gov.ua's
  masked codes are not unmasked).
- A ProZorro ФОП is not linked to a ЄДР ФОП record by name: namesakes are common, and the ЄДР dump has no code to join on.
- For individuals only region, locality and postal code are stored; street, house and flat from ProZorro are dropped at
  ingest (`src/lake_prozorro.py`). Full addresses are kept for legal entities only.
- The API response for a 10-digit query carries a note saying where the number comes from.

## How it is built

```
data.gov.ua ЄДР (UO, FOP), ЄРБ ──> src/lake.py build fop,uo,erb ──> uo/fop/erb.parquet
ProZorro contracts feed ─────────> src/lake_prozorro.py (incremental) ──> pz_contracts.parquet + prozorro_state.json
                                         │
                                         ▼
                         src/lake.py build index  (DuckDB, ~1 min)
                                         │
     registry_names_<stamp>.parquet   key = name without legal form       9.6 M rows   273 MB
     registry_ids_<stamp>.parquet     key = ЄДРПОУ / РНОКПП               2.0 M rows    70 MB
     registry_addr_<stamp>.parquet    key = locality / street word        grows with ProZorro
     registry_manifest.json           first/last key of every row group, normalisation tables
                                         │  GitHub release lake-latest (nightly, .github/workflows/lake.yml)
                                         ▼
     web/api/registry.js + web/api/_lake.js  (hyparquet, HTTP range requests)
```

- **Keys.** Upper-case, Ё→Е, Latin look-alikes→Cyrillic (state data often has `Свiтлана` with a Latin i), apostrophes
  dropped, other punctuation→space, leading legal forms (`ТОВ`, `ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ`, `ФОП` …)
  stripped. The same function exists in Python (`search_key`/`core_key`) and JS (`searchKey`/`coreKey`); the manifest
  carries the alphabet map, legal forms and address stop-words so the two cannot drift.
- **Lookup.** Each file is sorted by its key in row groups of 16,384 rows. The function binary-searches the manifest
  for the row groups that can hold the prefix, then reads at most 3 row groups (~0.5–2 MB) by range request. Address
  search looks up the rarest word (fewest row groups) and filters by the rest.
- **Measured locally** (Range-capable static server, real data): 35–180 ms per query, 0.5–3.9 MB read. Against the real
  GitHub release (dev machine, existing `uo.parquet`): redirect 0.2 s, Parquet footer 0.6 s (once per warm instance), a row group ~0.3–0.9 s. Responses are
  cached by the Vercel CDN for 1 h (`s-maxage=3600`).
- **Versioned files.** Index files carry a build stamp; the workflow uploads them, then the manifest, then deletes all
  but the two newest stamps, so an instance holding yesterday's manifest keeps working.
- **Ranking.** Exact key > whole-word prefix > prefix; registered (`зареєстровано`) above terminated; then the number of
  ProZorro contracts. 20 results.
- **Graph ids** follow `web/api/graph.js`: `registry:<ЄДРПОУ>` (ЄДР legal entity; `/api/node` now falls back to the lake
  when the company is not in `edr_orgs` yet), `fop:<RECORD>` (ЄДР ФОП record), `person:<full name lower-case>` (ФОП
  from ProZorro), `org:<name lower-case>` (ProZorro party with an 8-digit code missing from ЄДР).

## Why not Neon

Estimated size of an identifiers-only table in Postgres (code, type, name, region, id) for the 2.0 M ЄДРПОУ rows:
~81 B payload per row → heap ~250 MB + B-tree on code ~75 MB ≈ **~325 MB**. Even code + id alone would be >100 MB.
Neon is at ~400 MB of 1 GB, and the agreed ceiling for this feature was 60 MB, so the index stays in Parquet on the
release and **there is no Neon migration** (no `db/migrations/014_registry_ids.sql`). Trade-offs:

| | Parquet on GitHub release (chosen) | Neon table |
|---|---|---|
| Storage | 0 MB in Neon; ~345 MB on the release | ~325 MB+ (would put Neon at ~73 %) |
| Cold query | ~1.5–2 s (redirect + footer + row group) | ~50 ms |
| Warm query | ~0.3–0.9 s; CDN hit ~20 ms | ~20–50 ms |
| Freshness | nightly | nightly load job + writes |
| Failure mode | GitHub down → 502 from `/api/registry` only | shared with the whole site |

If GitHub latency becomes a problem, set `LAKE_BASE_URL` to any public object store that supports `Range` (R2, S3,
Backblaze) and upload the same files there; the code does not change.

## ProZorro backfill

The feed has no supplier field, so each contract costs one GET. Nightly the job spends `PROZORRO_MINUTES` (default 60):
first the day's changes (~20 k contracts, ~15 min), then history backwards from the first run (~60 k contracts/hour).
The full history therefore fills in over months. To speed it up, run the `lake` workflow manually with
`only=prozorro,index` and `prozorro_minutes=240` (the job timeout is 300 min). Progress is in `prozorro_state.json` on the
release (`back: null` = history done).

## Not done / next candidates

- Founders and signers (`people_roles`) are not in the name index: a person's name finds their ФОП, not the companies
  they founded. `/api/search` and `src/lake.py find` still cover that.
- НАЗК corruption register: legal entities with ЄДРПОУ could be added as a flag once it is reachable from the runner.
- Losing bidders (tender `bids`) also carry public codes; only signed contracts are ingested now.

## Національна поліція: розшук і зниклі безвісти (migration 021)

| Dataset | URL | Update | Stored | Size |
|---|---|---|---|---|
| Особи, які переховуються від органів влади | [data.gov.ua 5f3dbea6…](https://data.gov.ua/dataset/5f3dbea6-9c56-4c69-ae62-e2eba13f01f8) `PersonsWanted.json` | daily | every record: names (uk / ru / en), birth date, category, article, restraint, searching body, date, place → `wanted_persons` | 67 MB JSON, 75 491 records on 2026-10-22 |
| Особи, зниклі безвісти | [data.gov.ua 206741b9…](https://data.gov.ua/dataset/206741b9-2dc1-4aff-bbfa-c0a3fd254447) `BezvistiWanted.json` | daily | **counts only** (month of disappearance × oblast × sex × age band) → `missing_monthly` | 109 MB JSON, 126 305 records |

`src/police_wanted.py`, daily in `.github/workflows/opendata.yml`. Photos (≈ 5 GB for the wanted list, 4 GB for the missing)
and the police contact phones are not downloaded. A wanted record that disappears from the file gets `removed` = that day
(found, detained or withdrawn: the file does not say which); `wanted_daily` keeps per-day totals.

Derived fields: `art_group` from the first Criminal Code article (`military_evasion` ст. 335–336, 402–410; `war_collab`
ст. 109–114-2, 258, 260, 436–438; `border` 332; `corruption` 191, 209, 210, 354, 364–369-2; `violent`; `property`;
`other`; `no_article` = civil cases such as alimony); `agency` (police, sbu, dbr, nabu, border_guard) and `region` (oblast
slug, `crimea`, `russia`, `unknown` ≈ 4 %) from the searching body's name, else from the place.

Privacy: the wanted list is published by the police for public search, and the site shows only its published fields.
The person page and the chat dossier show a wanted record only on a **full-name** match (surname + first name, Ukrainian
or Russian spelling) and always with the birth date, labelled as a possible namesake. Missing persons are victims: no
names are stored.

On 2026-10-22: 19 554 records for evading service, AWOL or desertion (ст. 408 alone 11 404, ст. 407 7 604), 16 350 for
treason, collaboration and war crimes (ст. 111 9 124), 2 033 corruption; searching bodies: police 58 201, SBU 11 407,
DBR 5 300. Missing persons who disappeared since February 2022: 119 213.
