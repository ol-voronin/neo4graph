# Court summons notices: person → their cases

Status: 2026-10-22. Source added to `local/ua_worker.py` (`court_summons()`), run for every person query.

## Source

«Оголошення про виклик особи в суд та повідомлення про судове рішення», https://court.gov.ua/unknown/ (Судова влада
України). Courts must publish here a summons or a notice of a decision for a party whose last known address is on
temporarily occupied territory, in the former ATO zone, or unknown (ЦПК ст. 128, КАС ст. 129, КПК ст. 135, 297-5 for
in-absentia criminal proceedings). In practice: in-absentia criminal cases against collaborators and others who are
wanted, plus civil and administrative claims against people who cannot be served.

The page itself has **no captcha**. (The neighbouring «Стан розгляду справ», court.gov.ua/fair/, is a different service:
its search posts a reCAPTCHA token that the server checks, so it is **not** used.)

### Request

`POST https://court.gov.ua/unknown.php` (DataTables server-side protocol), form-encoded, after one `GET /unknown/` that
sets `PHPSESSID` / `SERVERID` (the API answers without them too):

| param | value |
|---|---|
| `sEcho` | page counter |
| `iColumns` | `10` |
| `iDisplayStart`, `iDisplayLength` | paging, 50 rows per page |
| `q_ver` | `arbitr` |
| `date` | `<from>~<to>` (dd.mm.yyyy) or `~` |
| `sSearch` | the person's name |
| `court`, `region` | `0` = all |
| `search_in_participants` | `false` (the named party); `true` searches every participant |
| `a`, `b`, `c` | empty (the page sends the text of hidden `#additional_*` elements, empty on the public page) |

Response: `{"sEcho", "aaData": [{"0": …, …, "9": …}], "total": false}`; no total count, so paging stops at a short page.
Row fields: `0` announced at, `1` court, `2` hearing date/time, `3` bench, `4` case number (inside a form that posts to
reyestr.court.gov.ua with `UserCourtCode`), `5` parties with roles («обвинувачений: …, Прокурор: …»), `6` claim,
`7` category («Колабораційна діяльність», «Пособництво державі-агресору»), `8` the notice text as HTML in a hidden
`div` (its `FILENAME` meta, e.g. `4819_41572340`, is a stable id), `9` the decision link.

### What the worker keeps

`{id, name, role, court, court_code, cause_num, proc_num, proc_nums, date, hearing, judges, parties, category,
notice_type, text, url}`; `proc_num` = the first 17-digit ЄРДР number in the notice text (often present in criminal
notices). Only notices where a party **is the queried person**: the same full name for a three-word query, surname + first
name for a two-word one (apostrophes normalised).

Hits: `source = "summons"`, `ref = id`, title «Виклик до суду · <court> · справа <cause_num>».

## Chaining

For each distinct case number found (at most 5 per person) the worker runs the case flow: ЄДРСР search by case number
(first page), the decisions go to `court_decisions` and as `reyestr` hits under the person's query, and up to 5 texts are
fetched, rulings («Ухвала») first, newest first — arrests of property are rulings. The run note counts texts with an
arrest of property. Names in those decisions are depersonalised by the register; the link to the person is the case
number from the court's own notice.

## Checked on 2026-10-22

| query | notices | cases | ЄРДР |
|---|---|---|---|
| Сальдо Володимир Васильович | 3 | 766/8230/24 (Херсонський апеляційний), 766/5247/24 (Херсонський міський) | 22024230000000196 |
| Стремоусов Кирило Сергійович | 18 | 757/6498/24-к (Печерський) and others | 12022000000000265 |
| Балицький Євген Віталійович | 27 | 333/7938/24 (collaboration), 331/47xx/23 (civil damages) and others | — |

Dry run: `python3 ua_worker.py --query "Сальдо Володимир Васильович" --kind person --dry-run` prints what would be sent
to `/api/ingest`.

## Limits

- Only people the courts could not serve: wanted persons, people in occupied territory, unknown addresses. Nothing for
  people who receive summons normally.
- Namesakes: a two-word query matches anyone with that surname and first name; the patronymic and the case category
  tell them apart.
- Polite rate: the worker's 2 s delay per request; at most 3 pages (150 notices) per name.
