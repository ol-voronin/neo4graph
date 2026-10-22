# Звіти Офісу Генерального прокурора (data.gov.ua)

Status: 2026-10-22. `src/ogp_reports.py` → `ogp_stats` / `ogp_keys` (migration 022) → views `ogp_ytd`, `ogp_monthly` →
`GET /api/crime`. Every number below was computed from the files downloaded on 2026-10-22.

## Sources

| form | dataset | what | periods in the file |
|---|---|---|---|
| `forma_1` | [Єдиний звіт про кримінальні правопорушення](https://data.gov.ua/dataset/a3fb85ad-56dc-496e-921a-9bc72f3a2e7a) | offences registered, suspicion served, by Criminal Code article and section; damages | 102 monthly reports, 01.2018–09.2026 |
| `forma_2` | [Єдиний звіт про осіб, які вчинили КП](https://data.gov.ua/dataset/09ac0204-8dde-4124-8f90-eac8f6952474) | persons served a suspicion / identified, by article | 96 reports, 08.2018–09.2026 |
| `forma_1_oz` | [Боротьба з організованими групами (1-ОЗ)](https://data.gov.ua/dataset/e523c258-f5be-48da-a2c3-0b17926c0987) | organised groups detected, members, duration, corrupt links | 104 reports, 2018–09.2026 |
| `forma_1_lft` | [Легалізація (відмивання) доходів (1-ЛФТ)](https://data.gov.ua/dataset/5ab13f1f-5179-443e-8cd1-377c7bee7513) | ст. 209, 209-1 and (2023+) 258-5, 306, 332, 439, 440 | quarterly 2018–2019, annual 2023–2025 |

`forma_5` (offences at enterprises, by type of economic activity) is not loaded yet: out of scope for the fact-check topics.

The files are normalized JSON: `cells` (table, row labels `level1Name…level5Name`, `metricCode`, `metricName`) and
`reports` (one per period; `values` keyed by cell id). **The layout changed several times** (2018, 2020 when кримінальні
проступки appeared, 2022, 2023), so the same quantity has different cell ids in different reports. Rows are therefore
matched by label, not by id:

- **article** `art:<n>` — a row whose last label ends with `ст. <n>` (`Дезертирство, ст.408`, `Колабораційна діяльність,
  ст. 111-1`) directly under `за видами правопорушень` in table 1 (all agencies). Part rows (`проступки (ч.ч. 1, 2 ст. 111-1)`)
  do not end with the article and are skipped.
- **section** `sec:<name>` — a whole Criminal Code section: either the one-label section row or the `Усього кримінальних
  правопорушень` row of that section's subtable. Since 2020 a section can appear as `Кримінальні правопорушення …`
  (crimes + misdemeanours) and as `Злочини …` (crimes only); the larger, the former, is kept. Names: `national_security`,
  `peace_war`, `military`, `official`, `economic`, `property`, `justice`, `life`, `public_safety`, `state_authority`.
- **total** — `Усього кримінальних правопорушень` of table 1.1 (or the layout without subtables).
- **sections_published** — the sum of every section row of the report (the check below).
- **detected_by:<agency>** — `Усього КП > які виявлено працівниками > органів …`: police, sbu, beb, dbr, nabu, prosecutors.
  The per-agency tables 2–6 are split into section subtables with no overall row, so they are not used.
- **money:** `bribe_uah_k` (сума неправомірної вигоди), `damage_uah_k` (встановлені збитки), `compensated_uah_k`, тис. грн,
  table 8.1 (all offences).

Measures: forma_1 `m1` → `registered` (обліковано у звітному періоді), `m2` → `suspicion` (КП, у яких вручено повідомлення
про підозру). The metric names drift (`Графа 1`, `Обліковано … зу ітному періоді`), the codes do not. forma_2 `m1` →
`persons_suspected`, `m5` → `persons_identified`. 1-ЛФТ: `registered`, `investigated`, `started`, `persons_identified`.

**Size.** `registered` is kept for every article; `suspicion` (forma_1) and all of forma_2 only for the curated war,
national-security and corruption articles (`WAR`, `CORRUPTION` in the script). 53 k rows ≈ 8 MB with the primary key
(measured on a Neon branch: 82 k synthetic rows of the same shape = 12 MB).

## Verification

**Sections add up to the total.** For 2018–2021 the sum of the published section rows equals `total` exactly
(December: 487 133, 444 130, 360 622, 321 443). This validates the row matching.

**Military crimes are not published since 2022.** From 2022 the section «проти встановленого порядку несення військової
служби» (розділ XIX, ст. 402–435: 407 СЗЧ, 408 дезертирство, 402 непокора …) is absent from every report, and so are
its articles; every other section is still there. The gap total − published sections is therefore the military section:

| year | total | published sections | gap = military (derived) |
|---|---|---|---|
| 2021 | 321 443 | 321 443 | 0 (published: 3 037) |
| 2022 | 362 636 | 348 870 | 13 766 |
| 2023 | 475 595 | 446 929 | 28 666 |
| 2024 | 492 479 | 398 579 | 93 900 |
| 2025 (Jan–Oct) | 524 552 | 337 566 | 186 986 |
| 2026 (Jan–Sep) | 444 750 | 275 588 | 169 162 |

`ogp_ytd` adds these as `sec:military` with `derived = true`. They are registered offences of the whole section (mostly
СЗЧ and desertion), not persons, and not split by article.

**Sanity series (registered, January–December unless marked):**

| key | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| art:111 державна зрада | 208 | 1 957 | 1 169 | 1 109 | 967 (Jan–Oct) | 690 (Jan–Sep) |
| art:111-1 колабораційна діяльність | — | 3 851 | 3 207 | 2 420 | 1 576 | 958 |
| art:336 ухилення від мобілізації | 0 | 1 108 | 3 784 | 4 147 | 2 498 | 663 |
| art:438 воєнні злочини | 172 | 60 387 | 60 944 | 28 788 | 43 027 | 59 586 |
| art:332 незаконне переправлення через кордон | 327 | 1 225 | 2 301 | 3 106 | 2 606 | 1 536 |
| art:369-2 зловживання впливом | 249 | 220 | 411 | 707 | 1 084 | 1 303 |
| art:191 привласнення, розтрата | 11 092 | 6 698 | 9 222 | 7 743 | 6 235 | 4 477 |
| detected_by:nabu | 101 | 119 | 231 | 274 | 308 | 294 |
| detected_by:dbr | 1 341 | 1 340 | 2 906 | 4 971 | 7 601 | 5 820 |

forma_2, persons served a suspicion: ст. 111-1 — 1 454 (2022), 1 592, 1 113, 680 (2025); ст. 336 — 1 935 (2023), 1 853,
1 185; ст. 369 (давання хабаря) — 1 219 (2022), 1 644, 1 658, 1 750 (2025).

## Caveats

- **National only.** No region in the open JSON; `/api/crime?region=` is ignored with a note.
- **Cumulative values.** `ogp_monthly` subtracts the previous report of the same year. A missing month (e.g. 01.2022,
  11–12.2025 in forma_1) makes the next increment cover several months (`months` > 1). Revised totals can make an
  increment negative (12.2021: −949).
- **Duplicate cells.** Where a layout repeats a row (section row and subtable total, crimes-only and all-offences variants)
  the larger value is kept; 982 such cases in forma_1, all in `sec:*` (the variants differ by the misdemeanours) and one
  `art:213` row in 01.2020.
- **forma_1_oz** labels changed between layouts (`Кількість осіб ОГ і ЗО / 3 особи` vs the longer wording), so one concept
  can sit under two keys in different years.
- **1-ЛФТ** 2018–2019 files carry `monthFrom = monthTo` (quarter ends); they are stored as published and stay out of the
  monthly view, which only uses series that start in January.

## Running

```
pip install ijson "psycopg[binary]" python-dotenv
python src/ogp_reports.py                 # download all four forms to data/ogp/, replace their rows
python src/ogp_reports.py --dry-run       # parse and print a summary
```

The forms are updated monthly around the 8th; a weekly run is enough. A parse that returns fewer than half of the
stored rows (a new layout the label rules do not recognise) is refused instead of wiping the form.
