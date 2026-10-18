"""НАЗК register of persons who committed corruption offences (open API getAllData). Downloads the JSON (~80 MB),
keeps records matching the names asked for, stores them in nazk_corrupt.  python src/nazk_corrupt.py --name "Прізвище Ім'я"
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
URL = "https://corruptinfo.nazk.gov.ua/ep/1.0/corrupt/getAllData"
CACHE = ROOT / "data" / "nazk" / "corrupt.json"


def load():
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not CACHE.exists() or CACHE.stat().st_size < 1e6:
        print("downloading", URL)
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)"})
        CACHE.write_bytes(urllib.request.urlopen(req, timeout=300).read())
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("data") or data.get("items") or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="comma-separated names (surname + first name)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    names = [" ".join(x.lower().split()[:2]) for x in a.name.split(",") if x.strip()]
    rows = load()
    print(f"register records: {len(rows)}")
    hits = []
    for r in rows:
        blob = json.dumps(r, ensure_ascii=False).lower()
        if any(all(part in blob for part in n.split()) for n in names):
            hits.append(r)
    print(f"matches: {len(hits)}")
    for h in hits[:10]:
        print(json.dumps(h, ensure_ascii=False)[:400])
    if a.dry_run or not hits:
        return
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS nazk_corrupt (id text PRIMARY KEY, query text, payload jsonb NOT NULL, created_at timestamptz DEFAULT now())")
    for h in hits:
        rid = str(h.get("id") or h.get("ID") or hash(json.dumps(h, sort_keys=True, ensure_ascii=False)))
        cur.execute("INSERT INTO nazk_corrupt(id, query, payload) VALUES (%s,%s,%s) ON CONFLICT (id) DO NOTHING", (rid, a.name, json.dumps(h, ensure_ascii=False)))
    conn.commit()


if __name__ == "__main__":
    main()
