"""Data lake: open-data dumps (and, later, offline files) as Parquet tables queried with DuckDB.

Why: the worker used to `unzip | grep` a 5 GB ФОП dump and a 0.7 GB debtors dump on every query (23 s – 3 min each).
Built once a day into Parquet with a normalised-name column, a lookup is a column scan of a few hundred MB, ~0.1–0.5 s.

  python src/lake.py build [--only fop,erb,uo] [--dir data/lake]     # download dumps, write Parquet
  python src/lake.py find "Гузенко Олександр Анатолійович"            # people/companies across all tables
  python src/lake.py find 24253556
  python src/lake.py build --only prozorro,index   # ProZorro increment + search index (fetches fop/uo/erb from the release if absent)

Tables (all have `name_key` = upper-case, single-spaced, ё/ї/і-normalised name):
  fop   — ЄДР фізичні особи-підприємці: record, name, stan, registration, terminated
  uo    — ЄДР юридичні особи: edrpou, name, short_name, opf, stan, founders[], beneficiaries[], signers[], capital, registration
  erb   — Єдиний реєстр боржників: name, birth_date, code, publisher, executor, vp, category
  people_roles — exploded: every founder/beneficiary/signer of `uo` as (name_key, edrpou, role) — company → people and back
  lawyers — Єдиний реєстр адвокатів України (НААУ via data.gov.ua, quarterly): name, council, certificate, status
            (діє / зупинено / припинено), disciplinary sanctions, city, organisational form. No contacts or street addresses
  pz_contracts — ProZorro contracts x suppliers (src/lake_prozorro.py, incremental): the only open source with a ФОП's РНОКПП
Search index for the site (`build --only index`, read by web/api/registry.js over HTTP range requests, see docs/registries.md):
  registry_names / registry_ids / registry_addr .parquet + registry_manifest.json
Offline data: drop CSV/XLSX/Parquet into data/lake/inbox/; `build` loads each file as table `inbox_<stem>` (all columns
kept, `name_key` added when a name-like column exists).
"""
import argparse
import csv
import html
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAKE = Path(__import__("os").environ.get("LAKE_DIR", ROOT / "data" / "lake"))
UA = "Mozilla/5.0 (research; rhetoric-strikes lake)"
DUMPS = {
    "fop": "https://data.gov.ua/dataset/03cc1239-3988-4451-aa0d-aadb77448714/resource/c262938f-cce7-4489-a805-2fd7c5a44e0b/download/fop.zip",
    "uo": "https://data.gov.ua/dataset/03cc1239-3988-4451-aa0d-aadb77448714/resource/d40cc921-39bb-44fd-be06-dc02589f45c6/download/uo.zip",
    "erb": "https://data.gov.ua/dataset/783b9b50-faba-4cc9-a393-60485e395b1d/resource/e6ea76c1-01f4-4bd0-a282-7d92d6ecc2a1/download/29-ex_csv_erb.zip",
}
BATCH = 200_000


def cap(s: str) -> str:
    """ДАР'Я ВЯЧЕСЛАВІВНА -> Дар'я Вячеславівна (str.title() capitalises after an apostrophe)."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in str(s or "").split())


def name_key(s: str) -> str:
    s = (s or "").upper().replace("Ё", "Е").replace("’", "'").replace("`", "'").replace('"', "").replace("«", "").replace("»", "")
    return re.sub(r"\s+", " ", s).strip()


def download(kind, max_age_h=20):
    p = LAKE / "raw" / f"{kind}.zip"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.stat().st_size > 1e6 and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return p
    print(f"[{kind}] downloading …", flush=True)
    t0 = time.time()
    tmp = p.with_suffix(".part")
    with urllib.request.urlopen(urllib.request.Request(DUMPS[kind], headers={"User-Agent": UA}), timeout=900) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 22):
            f.write(chunk)
    tmp.replace(p)
    print(f"[{kind}] {p.stat().st_size / 1e6:.0f} MB in {time.time() - t0:.0f}s", flush=True)
    return p


def _writer(kind, schema):
    import pyarrow.parquet as pq
    out = LAKE / f"{kind}.parquet.tmp"
    return out, pq.ParquetWriter(out, schema, compression="zstd")


def _flush(w, schema, rows):
    import pyarrow as pa
    if rows:
        w.write_table(pa.Table.from_pylist(rows, schema=schema))
        rows.clear()


TAG = re.compile(r"<(\w+)>(.*?)</\1>", re.S)


def _xml_lines(zpath):
    """Both ЄДР dumps put one <SUBJECT>…</SUBJECT> per line (cp1251); stream lines without loading the file."""
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if not n.lower().endswith(".xml"):
                continue
            with z.open(n) as raw:
                buf = b""
                for chunk in iter(lambda: raw.read(1 << 22), b""):
                    buf += chunk
                    *lines, buf = buf.split(b"</SUBJECT>")
                    for ln in lines:
                        i = ln.find(b"<SUBJECT>")
                        if i >= 0:
                            yield ln[i:].decode("cp1251", "ignore")


def _get(s, tag):
    m = re.search(f"<{tag}>(.*?)</{tag}>", s, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def _list(s, tag):
    return [html.unescape(x).strip() for x in re.findall(f"<{tag}>(.*?)</{tag}>", s, re.S) if x.strip()]


def build_fop(zpath):
    import pyarrow as pa
    schema = pa.schema([("record", pa.string()), ("name", pa.string()), ("name_key", pa.string()), ("stan", pa.string()),
                        ("registration", pa.string()), ("terminated", pa.string())])
    out, w = _writer("fop", schema)
    rows, n = [], 0
    for s in _xml_lines(zpath):
        nm = _get(s, "NAME")
        rows.append({"record": _get(s, "RECORD"), "name": cap(nm), "name_key": name_key(nm), "stan": _get(s, "STAN"),
                     "registration": _get(s, "REGISTRATION"), "terminated": _get(s, "TERMINATED_INFO")})
        n += 1
        if len(rows) >= BATCH:
            _flush(w, schema, rows)
            print(f"[fop] {n:,}", flush=True)
    _flush(w, schema, rows)
    w.close()
    out.replace(LAKE / "fop.parquet")
    return n


def build_uo(zpath):
    import pyarrow as pa
    L = pa.list_(pa.string())
    schema = pa.schema([("edrpou", pa.string()), ("name", pa.string()), ("short_name", pa.string()), ("name_key", pa.string()),
                        ("short_key", pa.string()), ("opf", pa.string()), ("stan", pa.string()), ("founders", L), ("beneficiaries", L),
                        ("signers", L), ("capital", pa.string()), ("registration", pa.string()), ("terminated", pa.string())])
    rschema = pa.schema([("name_key", pa.string()), ("name", pa.string()), ("edrpou", pa.string()), ("role", pa.string()), ("detail", pa.string())])
    out, w = _writer("uo", schema)
    rout, rw = _writer("people_roles", rschema)
    rows, roles, n = [], [], 0
    for s in _xml_lines(zpath):
        code = _get(s, "EDRPOU")
        f, b, sg = _list(s, "FOUNDER"), _list(s, "BENEFICIARY"), _list(s, "SIGNER")
        nm, sh = _get(s, "NAME"), _get(s, "SHORT_NAME")
        rows.append({"edrpou": code, "name": nm, "short_name": sh, "name_key": name_key(nm), "short_key": name_key(sh), "opf": _get(s, "OPF"),
                     "stan": _get(s, "STAN"), "founders": f, "beneficiaries": b, "signers": sg, "capital": _get(s, "AUTHORIZED_CAPITAL"),
                     "registration": _get(s, "REGISTRATION"), "terminated": _get(s, "TERMINATED_INFO")})
        if len(f) <= 60:  # giant cooperatives / ОСББ: thousands of members, pure namesake noise
            for role, lst in (("founder", f), ("beneficiary", b), ("signer", sg)):
                for x in lst:
                    who = re.split(r";| - ", x, maxsplit=1)[0].strip()
                    if who and not re.search(r"\d{8}", x.split(";")[1] if ";" in x else ""):
                        roles.append({"name_key": name_key(who), "name": cap(who), "edrpou": code, "role": role, "detail": x[:300]})
        n += 1
        if len(rows) >= BATCH:
            _flush(w, schema, rows)
            _flush(rw, rschema, roles)
            print(f"[uo] {n:,}", flush=True)
    _flush(w, schema, rows)
    _flush(rw, rschema, roles)
    w.close()
    rw.close()
    out.replace(LAKE / "uo.parquet")
    rout.replace(LAKE / "people_roles.parquet")
    return n


LAWYERS_DATASET = "e1af20b8-c80b-4f2c-bf82-566567ef936e"  # «Єдиний реєстр адвокатів» on data.gov.ua (the file name changes each quarter)


def download_lawyers(max_age_h=24 * 7):
    """The newest JSON resource of the dataset (CKAN package_show), ~230 MB."""
    p = LAKE / "raw" / "lawyers.json"
    if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return p
    meta = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"https://data.gov.ua/api/3/action/package_show?id={LAWYERS_DATASET}", headers={"User-Agent": UA}), timeout=60).read())
    res = [r for r in meta["result"]["resources"] if (r.get("format") or "").upper() == "JSON"]
    url = max(res, key=lambda r: r.get("last_modified") or r.get("created") or "")["url"]
    p.parent.mkdir(parents=True, exist_ok=True)
    print(f"[lawyers] downloading {url.rsplit('/', 1)[-1]} …", flush=True)
    tmp = p.with_suffix(".part")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=900) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(p)
    return p


def lawyer_status(flag, events):
    """The register's `status` is TRUE when the right to practise is restricted; the last event says how.
    >>> lawyer_status("FALSE", []), lawyer_status("TRUE", ["Право ... зупинено згідно п.1 ч.1 ст.31"]), lawyer_status("TRUE", ["... припинено ..."])
    ('діє', 'зупинено', 'припинено')
    """
    if flag != "TRUE":
        return "діє"
    last = str(events[-1]).lower() if events else ""
    return "припинено" if "припинено" in last else "зупинено" if "зупинено" in last else "обмежено"


def build_lawyers(path):
    import pyarrow as pa
    schema = pa.schema([("erau_id", pa.string()), ("name", pa.string()), ("name_key", pa.string()), ("council", pa.string()),
                        ("certificate", pa.string()), ("certificate_date", pa.string()), ("decision_date", pa.string()),
                        ("authority", pa.string()), ("status", pa.string()), ("status_note", pa.string()), ("sanctions", pa.int32()),
                        ("region", pa.string()), ("city", pa.string()), ("org_type", pa.string()), ("org_name", pa.string())])
    out, w = _writer("lawyers", schema)
    rows = []
    for x in json.loads(Path(path).read_text(encoding="utf-8")):
        name = " ".join(t for t in (x.get("familyName"), x.get("name"), x.get("additionalName")) if t).strip()
        if not name:
            continue
        ev = x.get("statusDescription") or []
        addr = (x.get("workAddress") or [{}])[0] or {}
        # one row per organisational form (individual practice, bureau, association): the link to ЄДР goes through org_name
        forms = [f for f in (x.get("orgforms") or []) if f] or [{}]
        for f in forms:
            org = (f.get("orgFormName") or "").strip()
            rows.append({"erau_id": str(x.get("ID")), "name": name, "name_key": name_key(name), "council": x.get("raName"),
                         "certificate": x.get("certificateNum"), "certificate_date": x.get("certificateDate"), "decision_date": x.get("decisionDate"),
                         "authority": x.get("authorityName"), "status": lawyer_status(x.get("status"), ev),
                         "status_note": str(ev[-1]).strip()[:300] if ev else None,
                         "sanctions": sum("стягнення" in str(e).lower() for e in ev), "region": addr.get("workAddressRegion"),
                         "city": addr.get("workAddressCity"), "org_type": f.get("orgFormType"),
                         "org_name": org if org and f.get("orgFormType") != "Індивідуальна адвокатська діяльність" else None})
    n = len(rows)  # _flush empties the list
    _flush(w, schema, rows)
    w.close()
    out.replace(LAKE / "lawyers.parquet")
    return n


def build_erb(zpath):
    import pyarrow as pa
    schema = pa.schema([("name", pa.string()), ("name_key", pa.string()), ("birth_date", pa.string()), ("code", pa.string()),
                        ("publisher", pa.string()), ("executor", pa.string()), ("vp", pa.string()), ("category", pa.string())])
    out, w = _writer("erb", schema)
    rows, n = [], 0
    with zipfile.ZipFile(zpath) as z:
        for fn in z.namelist():
            if not fn.lower().endswith(".csv"):
                continue
            with z.open(fn) as raw:
                rd = csv.reader(io.TextIOWrapper(raw, encoding="cp1251", errors="ignore", newline=""))
                next(rd, None)
                for r in rd:
                    if len(r) < 11:
                        continue
                    rows.append({"name": r[0], "name_key": name_key(r[0]), "birth_date": r[1], "code": r[2], "publisher": r[3],
                                 "executor": r[4], "vp": r[9], "category": r[10]})
                    n += 1
                    if len(rows) >= BATCH:
                        _flush(w, schema, rows)
                        print(f"[erb] {n:,}", flush=True)
    _flush(w, schema, rows)
    w.close()
    out.replace(LAKE / "erb.parquet")
    return n


def build_inbox(con):
    """Offline files: every CSV/XLSX/Parquet in data/lake/inbox becomes a table inbox_<stem> (a view over the file)."""
    inbox = LAKE / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    made = []
    for f in sorted(inbox.iterdir()):
        stem = re.sub(r"\W+", "_", f.stem.lower()).strip("_")
        src = {".csv": f"read_csv_auto('{f}', all_varchar=true)", ".parquet": f"read_parquet('{f}')",
               ".xlsx": f"read_xlsx('{f}', all_varchar=true)"}.get(f.suffix.lower())
        if not src:
            continue
        out = LAKE / f"inbox_{stem}.parquet"
        con.execute(f"COPY (SELECT * FROM {src}) TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
        made.append(out.name)
    return made


def connect():
    import duckdb
    con = duckdb.connect()
    for p in LAKE.glob("*.parquet"):
        con.execute(f"CREATE OR REPLACE VIEW {p.stem} AS SELECT * FROM read_parquet('{p}')")
    return con


def find(q, limit=50):
    """One call, every table: exact full name (all words, any order of the trailing ones) or ЄДРПОУ."""
    con = connect()
    tables = {p.stem for p in LAKE.glob("*.parquet")}
    out = {}
    if re.fullmatch(r"\d{8}", q.strip()):
        code = q.strip()
        if "uo" in tables:
            out["uo"] = con.execute("SELECT * FROM uo WHERE edrpou = ?", [code]).fetchdf()
        if "people_roles" in tables:
            out["people"] = con.execute("SELECT name, role, detail FROM people_roles WHERE edrpou = ?", [code]).fetchdf()
        if "erb" in tables:
            out["erb"] = con.execute("SELECT * FROM erb WHERE code = ? LIMIT ?", [code, limit]).fetchdf()
        return out
    k = name_key(q)
    like = k + "%"
    if "fop" in tables:
        out["fop"] = con.execute("SELECT * FROM fop WHERE name_key LIKE ? LIMIT ?", [like, limit]).fetchdf()
    if "erb" in tables:
        out["erb"] = con.execute("SELECT * FROM erb WHERE name_key LIKE ? LIMIT ?", [like, limit]).fetchdf()
    if "lawyers" in tables:
        out["lawyers"] = con.execute("SELECT * FROM lawyers WHERE name_key LIKE ? LIMIT ?", [like, limit]).fetchdf()
    if "people_roles" in tables:
        out["roles"] = con.execute("SELECT r.name, r.role, r.edrpou, u.short_name, u.stan FROM people_roles r LEFT JOIN uo u USING (edrpou) "
                                   "WHERE r.name_key LIKE ? LIMIT ?", [like, limit]).fetchdf()
    if "uo" in tables:
        ck = name_key(re.sub(r"^(ТОВ|ТЗОВ|ПАТ|ПРАТ|АТ|ТДВ|ДП|КП|ПП|ФГ|ГО|БФ)\s+", "", q.strip(), flags=re.I))
        out["uo"] = con.execute("SELECT edrpou, name, short_name, stan FROM uo WHERE short_key LIKE ? OR name_key LIKE ? LIMIT ?",
                                ["%" + ck + "%", "%" + ck + "%", limit]).fetchdf()
    for t in sorted(x for x in tables if x.startswith("inbox_")):
        cols = [c[0] for c in con.execute(f"DESCRIBE {t}").fetchall()]
        if cols:
            cond = " OR ".join(f'upper(CAST("{c}" AS VARCHAR)) LIKE ?' for c in cols)
            out[t] = con.execute(f"SELECT * FROM {t} WHERE {cond} LIMIT ?", ["%" + k + "%"] * len(cols) + [limit]).fetchdf()
    return out


# ------------------------------------------------------------------ search index for the site
# One key function shared with web/api/_lake.js (the manifest carries LATIN/CYR/FORMS/STOP so the two cannot drift):
# upper-case; Ё->Е and Latin look-alikes -> Cyrillic (mixed-script names like «Свiтлана» are common in state data);
# apostrophes dropped; every other non-alphanumeric run -> one space. Name keys also drop leading legal forms (ТОВ, ПП …).
LATIN, CYR = "ЁABCEHIKMOPTXY", "ЕАВСЕНІКМОРТХУ"
FORMS = ["ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ", "ТОВАРИСТВО З ДОДАТКОВОЮ ВІДПОВІДАЛЬНІСТЮ", "ПРИВАТНЕ АКЦІОНЕРНЕ ТОВАРИСТВО",
         "ПУБЛІЧНЕ АКЦІОНЕРНЕ ТОВАРИСТВО", "АКЦІОНЕРНЕ ТОВАРИСТВО", "ПРИВАТНЕ ПІДПРИЄМСТВО", "ДЕРЖАВНЕ ПІДПРИЄМСТВО",
         "КОМУНАЛЬНЕ НЕКОМЕРЦІЙНЕ ПІДПРИЄМСТВО", "КОМУНАЛЬНЕ ПІДПРИЄМСТВО", "КОМУНАЛЬНИЙ ЗАКЛАД", "ДОЧІРНЄ ПІДПРИЄМСТВО",
         "СЕЛЯНСЬКЕ ФЕРМЕРСЬКЕ ГОСПОДАРСТВО", "ФЕРМЕРСЬКЕ ГОСПОДАРСТВО", "ГРОМАДСЬКА ОРГАНІЗАЦІЯ", "БЛАГОДІЙНА ОРГАНІЗАЦІЯ",
         "БЛАГОДІЙНИЙ ФОНД", "ОБЄДНАННЯ СПІВВЛАСНИКІВ БАГАТОКВАРТИРНОГО БУДИНКУ", "ФІЗИЧНА ОСОБА ПІДПРИЄМЕЦЬ", "ФО П",
         "ТОВ", "ТЗОВ", "ПП", "ПАТ", "ПРАТ", "АТ", "ТДВ", "ДП", "КП", "КНП", "КЗ", "ФГ", "СФГ", "ГО", "БО", "БФ", "ОСББ", "ФОП", "ВАТ", "ЗАТ", "СПД"]
STOP = ["ВУЛ", "ВУЛИЦЯ", "БУД", "БУДИНОК", "КВАРТИРА", "МІСТО", "СЕЛО", "СЕЛИЩЕ", "СМТ", "ОБЛАСТЬ", "ОБЛ", "РАЙОН",
        "ПРОСП", "ПРОСПЕКТ", "ПРОВ", "ПРОВУЛОК", "ОФІС", "ПЛОЩА", "БУЛЬВ", "БУЛЬВАР", "ПРИМІЩЕННЯ", "ПРИМ", "КОРПУС", "УКРАЇНА",
        "МІСЬКА", "СІЛЬСЬКА", "СЕЛИЩНА", "ТЕРИТОРІАЛЬНА", "ГРОМАДА", "МІСЬКОГО", "ТИПУ", "ОБЛАСТІ", "РАЙОНУ"]
GROUP = 16_384  # rows per Parquet row group = the unit one range request fetches (~0.3-0.8 MB)


def search_key(s: str) -> str:
    s = (s or "").upper().translate(str.maketrans(LATIN, CYR))
    s = re.sub(r"['’ʼ`´‘]", "", s)
    return re.sub(r"[^0-9A-ZА-ЯЄІЇҐ]+", " ", s).strip()


def core_key(k: str) -> str:
    return re.sub(r"^(?:(?:" + "|".join(FORMS) + r") )+", "", k + " ").strip() or k


def fetch_release(name, max_age_h=26):
    """Reuse the last published table from the lake-latest release when this run does not rebuild it."""
    p = LAKE / name
    if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return p
    repo = __import__("os").environ.get("GITHUB_REPOSITORY", "ol-voronin/neo4graph")
    try:
        tmp = p.with_suffix(".part")
        req = urllib.request.Request(f"https://github.com/{repo}/releases/download/lake-latest/{name}", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=900) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 22):
                f.write(chunk)
        tmp.replace(p)
        print(f"[release] {name}: {p.stat().st_size / 1e6:.0f} MB", flush=True)
        return p
    except Exception as e:  # first run: the asset does not exist yet
        print(f"[release] {name}: {e}", flush=True)
        return p if p.exists() else None


def build_index():
    """registry_names (name prefix), registry_ids (ЄДРПОУ / РНОКПП exact), registry_addr (address token prefix),
    registry_people (founders / beneficiaries / signers by ЄДРПОУ) and registry_roles (the same by a person's name key).

    Only identifiers the source itself publishes:
      uo (ЄДР legal entities) — ЄДРПОУ, name, short name, status;
      fop (ЄДР ФОП)           — name, status, registration date. The dump has NO РНОКПП and NO address (checked 2026-10-22);
                                the ЄСВ registration number in EXCHANGE_DATA is deliberately not used as a tax number;
      pz_contracts (ProZorro) — supplier/buyer code (ЄДРПОУ, or РНОКПП of a ФОП), region, locality; street for legal entities only;
      erb (debtors)           — number of enforcement proceedings per ЄДРПОУ (codes of individuals are blank in the dump).
    Each file is sorted by its lookup key in row groups of GROUP rows; registry_manifest.json lists the first/last key of
    every row group, so the API binary-searches the manifest and fetches only the one or two row groups it needs.
    """
    import duckdb
    import pyarrow.parquet as pq
    for t in ("uo", "fop", "erb", "pz_contracts", "lawyers"):
        if not (LAKE / f"{t}.parquet").exists():
            fetch_release(f"{t}.parquet")
    have = {p.stem for p in LAKE.glob("*.parquet")}
    if not {"uo", "fop"} <= have:
        raise SystemExit("index needs uo.parquet and fop.parquet")
    (LAKE / "tmp").mkdir(exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{LAKE / 'tmp'}'; SET preserve_insertion_order=true")
    con.execute(f"""CREATE MACRO skey(s) AS trim(regexp_replace(regexp_replace(translate(upper(coalesce(s, '')), '{LATIN}', '{CYR}'),
                    '[''’ʼ`´‘]', '', 'g'), '[^0-9A-ZА-ЯЄІЇҐ]+', ' ', 'g'))""")
    con.execute(f"CREATE MACRO ckey(k) AS coalesce(nullif(trim(regexp_replace(k || ' ', '^(?:(?:{'|'.join(FORMS)}) )+', '')), ''), k)")
    con.execute("""CREATE MACRO pname(s) AS trim(regexp_replace(regexp_replace(s, '^\\s*(ФОП|ФО-П|ФІЗИЧНА ОСОБА[- ]ПІДПРИЄМЕЦЬ)[\\s"«]+', '', 'i'), '["«»]', '', 'g'))""")
    for t in ("uo", "fop", "erb", "pz_contracts", "lawyers"):
        if t in have:
            con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{LAKE / t}.parquet')")
    # ProZorro parties: one row per code with the latest name/address and the number of contracts won and placed
    if "pz_contracts" in have:
        con.execute("""CREATE TABLE pz AS
            WITH p AS (
              SELECT supplier_code AS code, supplier_name AS name, supplier_region AS region, supplier_locality AS locality,
                     supplier_street AS street, supplier_postal AS postal, date_signed AS d, contract_id, 1 AS sup FROM pz_contracts
              UNION ALL
              SELECT DISTINCT ON (contract_id) buyer_code, buyer_name, buyer_region, buyer_locality, buyer_street, NULL, date_signed, contract_id, 0
              FROM pz_contracts)
            SELECT code, CASE WHEN length(code) = 10 THEN 'rnokpp' ELSE 'edrpou' END AS code_type,
                   arg_max(name, d) AS name, arg_max(region, d) AS region, arg_max(locality, d) AS locality,
                   CASE WHEN length(code) = 8 THEN arg_max(street, d) END AS street, arg_max(postal, d) AS postal,
                   (count(DISTINCT contract_id) FILTER (sup = 1))::INT AS n_won, (count(DISTINCT contract_id) FILTER (sup = 0))::INT AS n_bought
            FROM p WHERE regexp_full_match(code, '\\d{8}|\\d{10}') GROUP BY code""")
    else:
        con.execute("CREATE TABLE pz (code VARCHAR, code_type VARCHAR, name VARCHAR, region VARCHAR, locality VARCHAR, street VARCHAR, postal VARCHAR, n_won INT, n_bought INT)")
    con.execute("CREATE TABLE debt AS " + ("SELECT code, count(*)::INT AS debts FROM erb WHERE regexp_full_match(code, '\\d{8}') GROUP BY code" if "erb" in have
                                           else "SELECT NULL::VARCHAR code, NULL::INT debts WHERE false"))
    # every entity once, with the columns the API returns; ids follow web/api/graph.js
    con.execute("""CREATE TABLE ent AS
        SELECT 'registry:' || u.edrpou AS id, 'org' AS kind, CASE WHEN skey(u.short_name) IN ('', 'НЕ МАЄ', 'НЕМАЄ', 'НЕМА', 'ВІДСУТНЯ', 'ВІДСУТНЄ', 'ВІДСУТНІЙ') THEN u.name ELSE u.short_name END AS name, u.name AS full_name, u.edrpou AS code,
               'edrpou' AS code_type, u.stan, split_part(u.registration, ';', 1) AS since, pz.region, pz.locality,
               concat_ws(', ', nullif(pz.postal, ''), nullif(pz.region, ''), nullif(pz.locality, ''), nullif(pz.street, '')) AS address,
               coalesce(pz.n_won, 0) AS n_won, coalesce(pz.n_bought, 0) AS n_bought, coalesce(d.debts, 0) AS debts,
               CASE WHEN pz.code IS NULL THEN 'edr' ELSE 'edr,prozorro' END AS src, u.opf
        FROM (SELECT * FROM uo WHERE regexp_full_match(edrpou, '\\d{8}')
              QUALIFY row_number() OVER (PARTITION BY edrpou ORDER BY stan = 'зареєстровано' DESC) = 1) u
        LEFT JOIN pz ON pz.code = u.edrpou LEFT JOIN debt d ON d.code = u.edrpou
        UNION ALL
        SELECT 'fop:' || record, 'fop', name, NULL, NULL, NULL, stan, split_part(registration, ';', 1), NULL, NULL, NULL, 0, 0, 0, 'edr', NULL
        FROM fop
        UNION ALL
        SELECT CASE WHEN code_type = 'rnokpp' THEN 'person:' || lower(regexp_replace(pname(name), '\\s+', ' ', 'g'))
                    ELSE 'org:' || lower(regexp_replace(trim(name), '\\s+', ' ', 'g')) END,
               CASE WHEN code_type = 'rnokpp' THEN 'fop' ELSE 'org' END, CASE WHEN code_type = 'rnokpp' THEN pname(name) ELSE name END,
               name, code, code_type, NULL, NULL, region, locality,
               concat_ws(', ', nullif(postal, ''), nullif(region, ''), nullif(locality, ''), nullif(street, '')), n_won, n_bought, 0, 'prozorro', NULL
        FROM pz WHERE code NOT IN (SELECT edrpou FROM uo)""")
    # lawyers (ЄРАУ): a person node per lawyer; stan carries the right-to-practise status, code the certificate number,
    # full_name the bureau / association. Not in registry_ids (a certificate is not a tax code)
    if "lawyers" in have:
        con.execute("""INSERT INTO ent SELECT DISTINCT ON (erau_id) 'person:' || lower(regexp_replace(trim(name), '\\s+', ' ', 'g')), 'lawyer', name,
                         org_name, certificate, 'cert', 'адвокат: ' || status, certificate_date, region, city, NULL, 0, 0, 0, 'erau', NULL
                       FROM lawyers ORDER BY erau_id, org_name IS NULL""")
    cols = "id, kind, name, full_name, code, code_type, stan, since, region, locality, address, n_won, n_bought, debts, src"
    order = "stan IS DISTINCT FROM 'зареєстровано', n_won + n_bought DESC, id"  # active first inside one key, then public-money ties
    queries = {
        "names": f"""SELECT DISTINCT ON (key, id) key, {cols} FROM (
                       SELECT ckey(skey(name)) AS key, * FROM ent WHERE name IS NOT NULL
                       UNION ALL SELECT ckey(skey(full_name)), * FROM ent WHERE kind = 'org' AND full_name IS NOT NULL
                       UNION ALL SELECT ckey(regexp_replace(skey(full_name), '^' || skey(opf) || ' ', '')), * FROM ent WHERE opf <> '')
                     WHERE key <> '' ORDER BY key, {order}""",
        "ids": f"SELECT {cols} FROM ent WHERE code IS NOT NULL AND code_type <> 'cert' ORDER BY code, {order}",
        "addr": f"""SELECT DISTINCT ON (token, id) token, {cols} FROM (
                      SELECT unnest(string_split(skey(concat_ws(' ', locality, address)), ' ')) AS token, * FROM ent WHERE address <> '' OR locality <> '')
                    WHERE length(token) >= 3 AND NOT regexp_full_match(token, '\\d+') AND token NOT IN ({', '.join(f"'{w}'" for w in STOP)})
                    ORDER BY token, {order}""",
    }
    # who stands behind a company and where a person appears (founder / beneficiary / signer), for the site's on-the-fly
    # neighbourhood of a registry hit that is not in the graph yet: people by ЄДРПОУ, companies by a person's search key
    if (LAKE / "people_roles.parquet").exists() or fetch_release("people_roles.parquet"):
        con.execute(f"CREATE VIEW people_roles AS SELECT * FROM read_parquet('{LAKE / 'people_roles'}.parquet')")
        queries["people"] = """SELECT edrpou AS code, role, name, left(detail, 200) AS detail FROM people_roles
                               WHERE regexp_full_match(edrpou, '\\d{8}') ORDER BY code, role, name"""
        lawyer_roles = ""
        if "lawyers" in have:
            # a lawyer's bureau / association matched to its ЄДР record by the name (the register has no ЄДРПОУ)
            con.execute("""CREATE TABLE lorg AS SELECT ckey(skey(name)) AS k, any_value(edrpou) AS edrpou FROM uo
                           WHERE regexp_full_match(edrpou, '\\d{8}') AND (upper(name) LIKE '%АДВОКАТ%' OR upper(coalesce(opf, '')) LIKE '%АДВОКАТ%')
                           GROUP BY 1 HAVING count(DISTINCT edrpou) = 1""")
            con.execute("""CREATE TABLE lroles AS SELECT DISTINCT l.name, CASE l.org_type WHEN 'Адвокатське бюро' THEN 'адвокат бюро' ELSE 'адвокат об''єднання' END AS role,
                             o.edrpou FROM lawyers l JOIN lorg o ON o.k = ckey(skey(l.org_name)) WHERE l.org_name IS NOT NULL""")
            lawyer_roles = " UNION ALL SELECT name, role, edrpou FROM lroles"
            queries["people"] = f"""SELECT code, role, name, detail FROM (
                                      SELECT edrpou AS code, role, name, left(detail, 200) AS detail FROM people_roles WHERE regexp_full_match(edrpou, '\\d{{8}}')
                                      UNION ALL SELECT edrpou, role, name, NULL FROM lroles) ORDER BY code, role, name"""
        queries["roles"] = f"""SELECT skey(r.name) AS key, r.name, r.role, r.edrpou AS code, e.name AS org, e.stan
                              FROM (SELECT name, role, edrpou FROM people_roles{lawyer_roles}) r LEFT JOIN ent e ON e.id = 'registry:' || r.edrpou
                              WHERE skey(r.name) <> '' ORDER BY key, e.stan IS DISTINCT FROM 'зареєстровано', code"""
    # versioned names: a reader holding the previous manifest keeps reading the previous files while new ones upload
    stamp = time.strftime("%Y%m%d%H%M")
    for old in LAKE.glob("registry_*.parquet"):
        old.unlink()
    manifest = {"built": time.strftime("%Y-%m-%dT%H:%M:%S"), "latin": LATIN, "cyr": CYR, "forms": FORMS, "stop": STOP, "files": {}}
    for name, q in queries.items():
        t0 = time.time()
        f = LAKE / f"registry_{name}_{stamp}.parquet"
        tmp = LAKE / f"registry_{name}_{stamp}.tmp"
        con.execute(f"COPY ({q}) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {GROUP})")
        tmp.replace(f)
        pf = pq.ParquetFile(f)
        keycol = {"names": "key", "ids": "code", "addr": "token", "people": "code", "roles": "key"}[name]
        groups, start = [], 0
        for i in range(pf.metadata.num_row_groups):
            k = pf.read_row_group(i, columns=[keycol]).column(0)
            groups.append([start, k[0].as_py(), k[-1].as_py()])
            start += len(k)
        manifest["files"][name] = {"file": f.name, "key": keycol, "size": f.stat().st_size, "rows": start, "groups": groups}
        print(f"[index] {f.name}: {start:,} rows, {len(groups)} row groups, {f.stat().st_size / 1e6:.0f} MB, {time.time() - t0:.0f}s", flush=True)
    manifest["counts"] = [list(r) for r in con.execute("SELECT kind, code_type, src, count(*) FROM ent GROUP BY ALL ORDER BY ALL").fetchall()]
    (LAKE / "registry_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
    return sum(v["rows"] for v in manifest["files"].values())


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--only", default="fop,uo,erb")
    f = sub.add_parser("find")
    f.add_argument("q")
    a = ap.parse_args()
    LAKE.mkdir(parents=True, exist_ok=True)
    if a.cmd == "build":
        for kind in a.only.split(","):
            t0 = time.time()
            if kind == "edrsr":  # ЄДРСР metadata + Postgres aggregates (src/lake_edrsr.py); years from EDRSR_YEARS, default current
                import subprocess
                subprocess.run([sys.executable, str(ROOT / "src" / "lake_edrsr.py"), "--years", __import__("os").environ.get("EDRSR_YEARS") or time.strftime("%Y")], check=True)
                continue
            if kind == "prozorro":  # incremental: yesterday's table + state from the release, then the feed since then
                import subprocess
                for f in ("pz_contracts.parquet", "prozorro_state.json"):
                    if not (LAKE / f).exists():
                        fetch_release(f)
                subprocess.run([sys.executable, str(ROOT / "src" / "lake_prozorro.py")], check=True)
                continue
            if kind == "index":
                print(f"[index] {build_index():,} rows in {time.time() - t0:.0f}s", flush=True)
                continue
            n = build_lawyers(download_lawyers()) if kind == "lawyers" else {"fop": build_fop, "uo": build_uo, "erb": build_erb}[kind](download(kind))
            print(f"[{kind}] {n:,} rows → {kind}.parquet ({(LAKE / f'{kind}.parquet').stat().st_size / 1e6:.0f} MB) in {time.time() - t0:.0f}s", flush=True)
        import duckdb
        print("inbox:", build_inbox(duckdb.connect()))
    else:
        t0 = time.time()
        res = find(a.q)
        for k, df in res.items():
            print(f"\n== {k}: {len(df)}")
            if len(df):
                print(df.head(15).to_string(max_colwidth=70))
        print(f"\n{time.time() - t0:.2f}s")


if __name__ == "__main__":
    main()
