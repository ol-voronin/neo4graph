"""ФОП card from the ЄДР dump (data lake table `fop`), for the chat's "знайди ФОП …" (queue kind `fop`).

Only the registry record itself: name, status, registration, termination. No chaining into courts, debts or other
registries. A full name (surname + first name, ideally + patronymic) is required; matches by name can be namesakes.

  python src/fop_lookup.py "Шевченко Тарас Григорович"
"""
import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

from lake import LAKE, UA, name_key

URL = "https://github.com/ol-voronin/neo4graph/releases/download/lake-latest/fop.parquet"
LIMIT = 20


def ensure_fop(max_age_h=26):
    p = LAKE / "fop.parquet"
    if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return p
    LAKE.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": UA}), timeout=900) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 22):
            f.write(chunk)
    tmp.replace(p)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    a = ap.parse_args()
    q = re.sub(r"\s+", " ", a.name.replace("ФОП", "").strip())
    if len(q.split()) < 2:
        raise SystemExit("потрібне повне імʼя: прізвище та імʼя (краще з по батькові)")
    import duckdb
    rows = duckdb.connect().execute(
        "SELECT record, name, stan, registration, terminated FROM read_parquet(?) WHERE name_key LIKE ? ORDER BY name LIMIT ?",
        [str(ensure_fop()), name_key(q) + "%", LIMIT + 1]).fetchall()
    more = len(rows) > LIMIT
    rows = rows[:LIMIT]
    print(f"{q}: {len(rows)}{'+' if more else ''} записів ФОП")
    from load_to_db import connect
    with connect() as conn, conn.cursor() as cur:
        for rec, nm, stan, reg, term in rows:
            cur.execute("INSERT INTO registry_hits(query, qkind, source, ref, title, snippet, url, payload) VALUES (%s,'fop','edr_fop',%s,%s,%s,NULL,%s) "
                        "ON CONFLICT (query, source, ref) DO UPDATE SET title = EXCLUDED.title, snippet = EXCLUDED.snippet, payload = EXCLUDED.payload",
                        (q, rec, f"ФОП {nm}", f"{stan or ''}; {reg or ''}", json.dumps({"record": rec, "name": nm, "stan": stan, "registration": reg, "terminated": term}, ensure_ascii=False)))
        conn.commit()


if __name__ == "__main__":
    main()
