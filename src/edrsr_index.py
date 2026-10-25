"""Court decisions from the ЄДРСР open-data dumps (data.gov.ua, one zip per year; the current year refreshes daily).
Streams documents.csv (tab-separated) and keeps rows that match (a) case numbers of interest, (b) selected courts
(HACC and its appeals chamber by default), writing them into court_decisions (migration 010). Full texts live at
doc_url on the register's open-data web server; fetched on demand for matched rows (--texts N).

  python src/edrsr_index.py --years 2026 --hacc                       # all HACC decisions this year
  python src/edrsr_index.py --years 2021-2026 --cause 642/8729/21,...  # specific case numbers across years
  python src/edrsr_index.py --years 2026 --from-db                    # case numbers referenced in ee_cases / statements
  python src/edrsr_index.py --years 2021-2026 --build-index            # VPS: keep the dumps, one parquet per year
  python src/edrsr_index.py --years 2026 --build-index --refresh       # VPS nightly: re-download the current year

With EDRSR_CACHE=1 (the VPS, deploy/vps.md) the dumps stay on disk and a year with a parquet index (data/edrsr/idx_<y>.parquet)
is matched with DuckDB in seconds instead of re-downloading the zip and scanning ~8 M rows in Python (minutes per year).
"""
import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.request
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASETS = {2021: "bcffb3ae-8e4f-4443-8058-d525257ab69e", 2022: "42c195b3-b915-4000-934a-7741825d1812",
            2023: "e0f2c66a-f773-442e-afcd-569be6a36c82", 2024: "1a6b5c08-0879-4387-8878-ac0027494274",
            2025: "2a95ae17-ed46-42ae-ad66-dd9b7a727bb7", 2026: "16ab7f06-7414-405f-8354-0a492475272d"}
UA = {"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)"}
CAUSE_RE = re.compile(r"\b\d{1,4}/\d{1,6}/\d{2}(?:-[\w/]+)?\b")
HACC_RE = re.compile(r"антикорупційн", re.I)


def api(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read())


def dump_url(year):
    p = api(f"https://data.gov.ua/api/3/action/package_show?id={DATASETS[year]}")["result"]
    for r in p["resources"]:
        if (r.get("format") or "").upper() == "ZIP":
            return r["url"]
    raise RuntimeError(f"no zip for {year}")


def download(year, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1e6:
        return dest
    url = dump_url(year)
    print("downloading", url)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return dest


def read_dict(z, name, key):
    out = {}
    with z.open(name) as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"), delimiter="\t"):
            out[row[key]] = row.get("name")
    return out


def stream(zip_path: Path, causes: set, court_codes: set, judges: tuple = (), category_re=None, form_re=None):
    with zipfile.ZipFile(zip_path) as z:
        names = {n.lower(): n for n in z.namelist()}
        courts = read_dict(z, names.get("courts.csv", "courts.csv"), "court_code")
        forms = read_dict(z, names.get("judgment_forms.csv", "judgment_forms.csv"), "judgment_code")
        kinds = read_dict(z, names.get("justice_kinds.csv", "justice_kinds.csv"), "justice_kind")
        cats = read_dict(z, names.get("cause_categories.csv", "cause_categories.csv"), "category_code")
        if "hacc" in court_codes:
            court_codes = {c for c, n in courts.items() if n and HACC_RE.search(n)} | (court_codes - {"hacc"})
            print("HACC court codes:", court_codes, [courts[c] for c in court_codes])
        with z.open(names.get("documents.csv", "documents.csv")) as f:
            n = 0
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"), delimiter="\t"):
                n += 1
                cause = (row.get("cause_num") or "").strip()
                jl = (row.get("judge") or "").lower() if judges else ""
                cat_ok = bool(category_re) and bool(category_re.search(cats.get(row.get("category_code")) or ""))
                if cat_ok and form_re and not form_re.search(forms.get(row.get("judgment_code")) or ""):
                    cat_ok = False
                if cause in causes or row.get("court_code") in court_codes or (judges and any(j in jl for j in judges)) or cat_ok:
                    yield {"doc_id": row["doc_id"], "cause_num": cause, "court_code": row.get("court_code"),
                           "court": courts.get(row.get("court_code")), "judgment_form": forms.get(row.get("judgment_code")),
                           "justice_kind": kinds.get(row.get("justice_kind")), "category": cats.get(row.get("category_code")),
                           "adjudication_date": row.get("adjudication_date") or None, "receipt_date": row.get("receipt_date") or None,
                           "judge": (row.get("judge") or "").strip() or None, "doc_url": row.get("doc_url"),
                           "status": row.get("status"), "date_publ": row.get("date_publ") or None}
            print(f"scanned {n} rows")


CACHE = os.environ.get("EDRSR_CACHE") == "1"
COLS = ["doc_id", "cause_num", "court_code", "court", "judgment_form", "justice_kind", "category", "adjudication_date",
        "receipt_date", "judge", "doc_url", "status", "date_publ"]


def idx_path(y):
    return ROOT / "data" / "edrsr" / f"idx_{y}.parquet"


def build_index(y, refresh=False):
    """Every row of the year's dump, names joined in, as parquet (zstd). Re-downloads the zip with refresh."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    zp = ROOT / "data" / "edrsr" / f"edrsr_data_{y}.zip"
    if refresh:
        zp.unlink(missing_ok=True)
    zp = download(y, zp)
    out, tmp = idx_path(y), idx_path(y).with_suffix(".tmp")
    schema = pa.schema([(c, pa.string()) for c in COLS])
    t0, n = time.time(), 0
    with pq.ParquetWriter(tmp, schema, compression="zstd") as w:
        batch = []
        # every row: a match-all predicate through the same stream (same joins and cleaning as a normal scan)
        for rec in stream(zp, set(), set(), (), re.compile(""), None):
            batch.append(rec)
            if len(batch) >= 200_000:
                w.write_table(pa.Table.from_pylist(batch, schema)); n += len(batch); batch = []
        if batch:
            w.write_table(pa.Table.from_pylist(batch, schema)); n += len(batch)
    tmp.replace(out)
    print(f"index {out.name}: {n} rows, {out.stat().st_size / 1e6:.0f} MB, {time.time() - t0:.0f} s")


def match_index(y, causes, court_codes, judges, category_re, form_re):
    """The stream() predicate over the parquet index (DuckDB): case numbers, court codes (or «hacc»), judge fragments, category."""
    import duckdb
    db = duckdb.connect()
    db.execute("CREATE TEMP TABLE c(cause text)")
    if causes:
        db.executemany("INSERT INTO c VALUES (?)", [(x,) for x in causes])
    conds, args = ["cause_num IN (SELECT cause FROM c)"], []
    codes = set(court_codes) - {"hacc"}
    if codes:
        conds.append("court_code IN (" + ",".join("?" * len(codes)) + ")"); args += list(codes)
    if "hacc" in court_codes:
        conds.append("regexp_matches(court, ?, 'i')"); args.append(HACC_RE.pattern)
    for j in judges:
        conds.append("contains(lower(judge), ?)"); args.append(j)
    if category_re:
        conds.append("(regexp_matches(coalesce(category, ''), ?, 'i')" + (" AND regexp_matches(coalesce(judgment_form, ''), ?, 'i')" if form_re else "") + ")")
        args += [category_re.pattern] + ([form_re.pattern] if form_re else [])
    q = f"SELECT {', '.join(COLS)} FROM read_parquet(?) WHERE " + " OR ".join(conds)
    t0 = time.time()
    rows = db.execute(q, [str(idx_path(y))] + args).fetchall()
    print(f"index {y}: {len(rows)} rows in {time.time() - t0:.1f} s")
    for r in rows:
        rec = dict(zip(COLS, r))
        for k in ("adjudication_date", "receipt_date", "date_publ"):
            rec[k] = rec[k] or None
        yield rec


def rtf_to_text(data: bytes) -> str:
    """Minimal RTF -> text: handles \\'hh (cp1251) and \\uNNNN escapes, drops control words and groups."""
    s = data.decode("latin-1", "replace")
    out, i, n, skip_depth, depth = [], 0, len(s), None, 0
    while i < n:
        c = s[i]
        if c == "{":
            depth += 1; i += 1
            if s.startswith("\\*", i) or s.startswith("\\fonttbl", i) or s.startswith("\\colortbl", i) or s.startswith("\\stylesheet", i) or s.startswith("\\info", i):
                if skip_depth is None:
                    skip_depth = depth
            continue
        if c == "}":
            if skip_depth is not None and depth == skip_depth:
                skip_depth = None
            depth -= 1; i += 1; continue
        if c == "\\":
            m = re.match(r"\\'([0-9a-fA-F]{2})", s[i:i + 4])
            if m:
                if skip_depth is None:
                    out.append(bytes([int(m.group(1), 16)]).decode("cp1251", "replace"))
                i += 4; continue
            m = re.match(r"\\u(-?\d+)\s?", s[i:i + 10])
            if m:
                if skip_depth is None:
                    out.append(chr(int(m.group(1)) % 65536))
                i += m.end()
                if i < n and s[i] not in "\\{}":
                    i += 1  # skip the fallback char
                continue
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", s[i:i + 30])
            if m:
                w = m.group(1)
                if skip_depth is None and w in ("par", "line", "pard"):
                    out.append("\n")
                elif skip_depth is None and w == "tab":
                    out.append(" ")
                i += m.end(); continue
            i += 2; continue
        if skip_depth is None and c not in "\r\n":
            out.append(c)
        i += 1
    txt = "".join(out).replace("\x00", "")
    txt = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", txt)
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", txt)).strip()


def fetch_text(url):
    try:
        data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:120]
    if data[:5] == b"{\\rtf" or url.lower().endswith(".rtf"):
        return rtf_to_text(data)[:200000], None
    html = data.decode("utf-8", "replace")
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", txt)).strip()
    return txt[:200000], None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2026")
    ap.add_argument("--cause", default="")
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--hacc", action="store_true")
    ap.add_argument("--courts", default="", help="extra court codes, comma-separated")
    ap.add_argument("--texts", type=int, default=0, help="fetch full text for up to N matched decisions without text")
    ap.add_argument("--judge", default="", help="judge name fragment(s), comma-separated")
    ap.add_argument("--category", default="", help="regex on the case category name (e.g. 'земел'), indexes every matching decision of the year")
    ap.add_argument("--form", default="", help="regex on judgment form, combined with --category (e.g. 'Вирок|Рішення')")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--build-index", action="store_true", help="write data/edrsr/idx_<year>.parquet from the dump (VPS)")
    ap.add_argument("--refresh", action="store_true", help="with --build-index: re-download the dump first")
    a = ap.parse_args()
    judges = tuple(" ".join(x.lower().split()[:2]) for x in a.judge.split(",") if x.strip())
    category_re = re.compile(a.category, re.I) if a.category else None
    form_re = re.compile(a.form, re.I) if a.form else None
    years = []
    for part in a.years.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            years += list(range(int(lo), int(hi) + 1))
        elif part.strip():
            years.append(int(part))
    if a.build_index:
        for y in years:
            build_index(y, a.refresh)
        return
    causes = {c.strip() for c in a.cause.split(",") if c.strip()}
    court_codes = {c.strip() for c in a.courts.split(",") if c.strip()}
    if a.hacc:
        court_codes.add("hacc")
    conn = cur = None
    if a.from_db or not a.dry_run:
        sys.path.insert(0, str(ROOT / "src"))
        from load_to_db import connect
        conn = connect()
        cur = conn.cursor()
    if a.from_db:
        cur.execute("SELECT ref FROM ee_cases WHERE ref IS NOT NULL UNION SELECT quote FROM statements UNION "
                    "SELECT payload::text FROM entity_extractions WHERE is_case_related")
        for (t,) in cur.fetchall():
            causes |= set(CAUSE_RE.findall(t or ""))
        print(f"case numbers from db: {len(causes)}")
    print(f"years {years}, causes {len(causes)}, courts {court_codes}, judges {judges}")
    if not causes and not court_codes and not judges and not category_re:
        print("nothing to match"); return
    total = 0
    for y in years:
        fast = CACHE and idx_path(y).exists()
        zp = None if fast else download(y, ROOT / "data" / "edrsr" / f"edrsr_data_{y}.zip")
        for rec in (match_index(y, causes, court_codes, judges, category_re, form_re) if fast else stream(zp, causes, court_codes, judges, category_re, form_re)):
            total += 1
            if a.dry_run:
                if total <= 20:
                    print(json.dumps(rec, ensure_ascii=False)[:300])
                continue
            cur.execute("INSERT INTO court_decisions(doc_id, cause_num, court_code, court, judgment_form, justice_kind, category, "
                        "adjudication_date, receipt_date, judge, doc_url, status, date_publ, year) VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (doc_id) DO UPDATE SET status=EXCLUDED.status, "
                        "date_publ=EXCLUDED.date_publ, judge=EXCLUDED.judge",
                        (rec["doc_id"], rec["cause_num"], rec["court_code"], rec["court"], rec["judgment_form"], rec["justice_kind"],
                         rec["category"], rec["adjudication_date"], rec["receipt_date"], rec["judge"], rec["doc_url"], rec["status"],
                         rec["date_publ"], y))
            if total % 500 == 0:
                conn.commit()
        if conn:
            conn.commit()
        if zp and not CACHE:
            zp.unlink(missing_ok=True)  # runners have limited disk; the dump is re-downloaded next time
    print(f"matched {total} decisions")
    if a.texts and cur:
        cur.execute("SELECT doc_id, doc_url FROM court_decisions WHERE text IS NULL AND doc_url IS NOT NULL AND status='1' "
                    + ("AND cause_num = ANY(%s) " if causes else "") + "ORDER BY adjudication_date DESC NULLS LAST LIMIT %s",
                    ((list(causes), a.texts) if causes else (a.texts,)))
        rows = cur.fetchall()
        ok = 0
        for doc_id, url in rows:
            txt, err = fetch_text(url)
            if txt:
                txt = txt.replace("\x00", "")
            if txt and len(txt) > 200:
                cur.execute("UPDATE court_decisions SET text=%s, text_fetched_at=now() WHERE doc_id=%s", (txt, doc_id))
                ok += 1
            else:
                cur.execute("UPDATE court_decisions SET text_error=%s, text_fetched_at=now() WHERE doc_id=%s", (err or "empty", doc_id))
            conn.commit()
            time.sleep(1.0)
        print(f"texts fetched {ok}/{len(rows)}")


if __name__ == "__main__":
    main()
