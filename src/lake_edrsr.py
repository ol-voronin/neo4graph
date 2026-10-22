"""ЄДРСР metadata for all justice kinds (civil incl. family, criminal, administrative, commercial, admin offences) in the lake,
plus small statistics tables in Postgres for the chat (`court_stats`).

One year is ~7.5 M decisions / ~3.6 M cases: far too much for Neon (0.5 GB), so
  - the full metadata goes to Parquet: data/lake/edrsr_<year>.parquet (published with the nightly lake release);
  - Postgres gets only aggregates (coded, names in edrsr_dict, views with names): edrsr_stats (decisions by kind × category × region × form) and
    edrsr_case_stats (cases by kind × category × region: how many reached a verdict / decision, median duration).
The dump has no party names: court, judge, category, dates, form and the URL of the text only.

  python src/lake_edrsr.py --years 2026            # current year (nightly)
  python src/lake_edrsr.py --years 2021-2025       # backfill, once
  python src/lake_edrsr.py --years 2026 --no-db    # Parquet only
"""
import argparse
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from edrsr_index import dump_url
from lake import LAKE, UA

# Category/region/form names live once in edrsr_dict; the tables keep smallint codes (names repeated in every row were
# ~100 MB). Views edrsr_stats / edrsr_case_stats with the old columns are in db/migrations/017_edrsr_compact.sql.
DDL = """
CREATE TABLE IF NOT EXISTS edrsr_dict (
  id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, field "char" NOT NULL, value text NOT NULL, UNIQUE (field, value));
CREATE TABLE IF NOT EXISTS edrsr_stats_c (
  year smallint NOT NULL, jk smallint, cat smallint, reg smallint, frm smallint, n int NOT NULL);
CREATE INDEX IF NOT EXISTS edrsr_stats_c_idx ON edrsr_stats_c (year, jk);
CREATE TABLE IF NOT EXISTS edrsr_case_stats_c (
  year smallint NOT NULL, jk smallint, cat smallint, reg smallint,
  cases int NOT NULL, with_verdict int, with_decision int, with_court_order int, median_days real, p75_days real);
CREATE INDEX IF NOT EXISTS edrsr_case_stats_c_idx ON edrsr_case_stats_c (year, jk);"""


def years(spec):
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def download(year, max_age_h=20):
    p = LAKE / "raw" / f"edrsr_{year}.zip"
    p.parent.mkdir(parents=True, exist_ok=True)
    current = year >= time.localtime().tm_year
    if p.exists() and p.stat().st_size > 1e6 and (not current or time.time() - p.stat().st_mtime < max_age_h * 3600):
        return p
    print(f"[edrsr {year}] downloading …", flush=True)
    tmp = p.with_suffix(".part")
    with urllib.request.urlopen(urllib.request.Request(dump_url(year), headers={"User-Agent": UA}), timeout=1800) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 22):
            f.write(chunk)
    tmp.replace(p)
    return p


def build(year, con, work: Path):
    """documents.csv + dictionaries -> one denormalised Parquet; returns the DuckDB view name for aggregation."""
    with zipfile.ZipFile(download(year)) as z:
        for n in z.namelist():
            if n.endswith(".csv"):
                with z.open(n) as src, open(work / Path(n).name, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 22)
    rd = lambda f: f"read_csv('{work / f}', delim='\t', header=true, all_varchar=true, quote='\"', ignore_errors=true)"  # noqa: E731
    con.execute(f"""CREATE OR REPLACE TEMP VIEW d AS
      SELECT doc.doc_id, doc.cause_num, doc.court_code, c.name AS court, r.name AS region, c.instance_code AS instance,
             f.name AS form, k.name AS justice_kind, cat.name AS category,
             try_cast(left(doc.adjudication_date, 10) AS date) AS adjudication_date, try_cast(left(doc.receipt_date, 10) AS date) AS receipt_date,
             doc.judge, doc.doc_url, doc.status
      FROM {rd('documents.csv')} doc
      LEFT JOIN {rd('courts.csv')} c USING (court_code)
      LEFT JOIN {rd('regions.csv')} r ON r.region_code = c.region_code
      LEFT JOIN {rd('judgment_forms.csv')} f USING (judgment_code)
      LEFT JOIN {rd('justice_kinds.csv')} k USING (justice_kind)
      LEFT JOIN {rd('cause_categories.csv')} cat USING (category_code)""")
    out = LAKE / f"edrsr_{year}.parquet"
    con.execute(f"COPY (SELECT * FROM d) TO '{out}.tmp' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 500000)")
    os.replace(f"{out}.tmp", out)
    con.execute(f"CREATE OR REPLACE TEMP VIEW y AS SELECT * FROM read_parquet('{out}')")
    n = con.execute("SELECT count(*) FROM y").fetchone()[0]
    print(f"[edrsr {year}] {n:,} decisions → {out.name} ({out.stat().st_size / 1e6:.0f} MB)", flush=True)
    return n


def aggregates(year, con):
    stats = con.execute("""SELECT justice_kind, category, region, form, count(*)::int FROM y GROUP BY ALL""").fetchall()
    # one row per case: its dominant kind/category/region, first and last decision, which final forms it reached
    cases = con.execute("""
      WITH c AS (
        SELECT cause_num, mode(justice_kind) jk, mode(category) cat, mode(region) reg,
               min(adjudication_date) d0, max(adjudication_date) d1,
               bool_or(form = 'Вирок') v, bool_or(form IN ('Рішення', 'Додаткове рішення')) r, bool_or(form = 'Судовий наказ') o, count(*) n
        FROM y WHERE cause_num IS NOT NULL AND cause_num <> '' GROUP BY cause_num)
      SELECT jk, cat, reg, count(*)::int, count(*) FILTER (WHERE v)::int, count(*) FILTER (WHERE r)::int, count(*) FILTER (WHERE o)::int,
             median(d1 - d0) FILTER (WHERE n > 1), quantile_cont(d1 - d0, 0.75) FILTER (WHERE n > 1)
      FROM c GROUP BY ALL""").fetchall()
    return stats, cases


def codes(cur, rows, fields):
    """name -> smallint code per field ('k', 'c', 'r', 'f'), adding names not yet in edrsr_dict."""
    vals = {(f, r[i]) for r in rows for i, f in enumerate(fields) if f and i < len(r) and isinstance(r[i], str)}
    cur.executemany("INSERT INTO edrsr_dict (field, value) VALUES (%s, %s) ON CONFLICT DO NOTHING", sorted(vals))
    cur.execute("SELECT field, value, id FROM edrsr_dict")
    d = {(f, v): i for f, v, i in cur.fetchall()}
    return lambda f, v: None if v is None else d[(f, v)]


def write_db(year, stats, cases):
    from load_to_db import connect
    with connect() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        code = codes(cur, [*stats, *(r[:3] for r in cases)], ("k", "c", "r", "f"))
        cur.execute("DELETE FROM edrsr_stats_c WHERE year = %s", (year,))
        cur.execute("DELETE FROM edrsr_case_stats_c WHERE year = %s", (year,))
        with cur.copy("COPY edrsr_stats_c (year, jk, cat, reg, frm, n) FROM STDIN") as cp:
            for k, c, r, f, n in stats:
                cp.write_row((year, code("k", k), code("c", c), code("r", r), code("f", f), n))
        with cur.copy("COPY edrsr_case_stats_c (year, jk, cat, reg, cases, with_verdict, with_decision, with_court_order, median_days, p75_days) FROM STDIN") as cp:
            for r in cases:
                cp.write_row((year, code("k", r[0]), code("c", r[1]), code("r", r[2]), *r[3:7],
                              None if r[7] is None else float(r[7]), None if r[8] is None else float(r[8])))
        conn.commit()
    print(f"[edrsr {year}] Postgres: {len(stats):,} stats rows, {len(cases):,} case-stats rows", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default=str(time.localtime().tm_year))
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args()
    import duckdb
    LAKE.mkdir(parents=True, exist_ok=True)
    for y in years(a.years):
        t0 = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            con = duckdb.connect()
            con.execute(f"SET temp_directory = '{tmp}'")
            build(y, con, Path(tmp))
            stats, cases = aggregates(y, con)
            con.close()
        if not a.no_db:
            write_db(y, stats, cases)
        print(f"[edrsr {y}] done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
