"""Data lake: open-data dumps (and, later, offline files) as Parquet tables queried with DuckDB.

Why: the worker used to `unzip | grep` a 5 GB ФОП dump and a 0.7 GB debtors dump on every query (23 s – 3 min each).
Built once a day into Parquet with a normalised-name column, a lookup is a column scan of a few hundred MB, ~0.1–0.5 s.

  python src/lake.py build [--only fop,erb,uo] [--dir data/lake]     # download dumps, write Parquet
  python src/lake.py find "Гузенко Олександр Анатолійович"            # people/companies across all tables
  python src/lake.py find 24253556

Tables (all have `name_key` = upper-case, single-spaced, ё/ї/і-normalised name):
  fop   — ЄДР фізичні особи-підприємці: record, name, stan, registration, terminated
  uo    — ЄДР юридичні особи: edrpou, name, short_name, opf, stan, founders[], beneficiaries[], signers[], capital, registration
  erb   — Єдиний реєстр боржників: name, birth_date, code, publisher, executor, vp, category
  people_roles — exploded: every founder/beneficiary/signer of `uo` as (name_key, edrpou, role) — company → people and back
Offline data: drop CSV/XLSX/Parquet into data/lake/inbox/; `build` loads each file as table `inbox_<stem>` (all columns
kept, `name_key` added when a name-like column exists).
"""
import argparse
import csv
import html
import io
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
        rows.append({"record": _get(s, "RECORD"), "name": nm.title(), "name_key": name_key(nm), "stan": _get(s, "STAN"),
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
                        roles.append({"name_key": name_key(who), "name": who.title(), "edrpou": code, "role": role, "detail": x[:300]})
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
            n = {"fop": build_fop, "uo": build_uo, "erb": build_erb}[kind](download(kind))
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
