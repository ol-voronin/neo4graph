# ARMA: arrested assets register (migration 024)

Source: «Єдиний державний реєстр активів, на які накладено арешт у кримінальному провадженні» (ARMA, data.gov.ua dataset
`d6cc6909…`, `edra_opendata.json`, ~740 MB, updated weekly). Loader: `src/arma_assets.py`, weekly on Mondays in
`.github/workflows/opendata.yml` (manual run: `gh workflow run opendata -f what=arma`).

## What the open register has and lacks

- One row per **court decision or ARMA action** about an asset (795 159 rows, 528 040 assets on 2026-10-16).
- Per row: asset description, `props` («Марка: … | Модель: … | Рік випуску: …», «Площа: …»), decision type and date, **case
  numbers** (`docnum`: the investigating judge's case and the ruling's own number, e.g. `757/26995/19-к, 1-кс-50776/19`),
  the **ЄРДР number** of the criminal proceedings, court / judge / investigator / prosecutor, ARMA's own actions (management
  tender, valuation, sale, deposit) with valuation and income.
- **No owner** (person or company), no full address, no VIN / plate, no cadastral number (masked as `**********:**:***:****`).
  An asset links to a person only through its case numbers or ЄРДР number: person → their criminal case (court register,
  media) → assets arrested in that case. Several suspects in one case cannot be told apart from the register alone.

## What is stored

`arma_assets`: one row per asset, folded from its decisions.

| column | meaning |
|---|---|
| `kind` | vehicle, real_estate, land, shares, vessel, aircraft (classified from description and props) |
| `brand`, `model`, `year` | from props (vehicles; 74 k of 79 k vehicles have a brand, 17 k a year) |
| `area` | m², hectares converted (real estate and land) |
| `region` | oblast slug when the description names a place (most descriptions only say «за адресою м. Київ» or nothing) |
| `docnums`, `erdr` | every case number / ЄРДР number on the asset's decisions; GIN-indexed — query with `&&` (`WHERE docnums && ARRAY['757/26995/19-к']`, 0.3 ms); `= ANY(docnums)` does not use the index (seq scan, ~30 ms) |
| `last_action`, `last_decision`, `first_decision` | latest decision code (arrest, arrest_partly, management, sale, forfeited, lifted, lifted_partly, management_cancelled, other) and dates |
| `state`, `valuation`, `income` | ARMA's latest action state; valuation (UAH, largest reported); action income |
| `units` | identical lots folded into one row (same description, props and case numbers: a fleet or 40 gyroscopes seized together) |

`arma_counts`: every asset by kind and ARMA state, including what is not stored row by row.

Size budget (Neon free plan, 1 GB): **54–57 MB with indexes** for 117 657 rows (139 348 assets) — measured on a Neon branch.
To get there:

- **not stored row by row** (counted in `arma_counts` only): seized goods (299 783: boxes, phones, documents, cards, laptops,
  driver's licences…), money lots (42 107: cash and accounts without a counterparty), railcars (23 905);
- assets whose **latest decision lifted the arrest** (`скасування арешту майна`, 22 897) are dropped, counted as `<kind>_lifted`;
- identical lots folded (`units`); description cut to 120 chars; raw props kept only when nothing structured was extracted;
  courts not stored (the court is the first segment of the case number: 757 Печерський, 991 ВАКС, …).

## Caveats

- The file is not valid JSON as a whole (`actionincomesum` uses a decimal comma); it is read line by line.
- Classification is by keywords: a description that lists several things is classified by the first match; ~0.1 % noise.
- An arrest does not have to reach ARMA: e.g. the HACC ruling in case 991/9492/26 (a flat, an Audi Q8, accounts) is not in the
  register. Court decisions remain the primary source of what was arrested; ARMA adds what was transferred to management,
  valued, sold or forfeited.
- Reports describe the asset as the investigator wrote it; brand / model spellings vary («TOYOTA  », «Тойота»).
