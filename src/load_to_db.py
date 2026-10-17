"""Load data into Postgres (Neon). Needs DATABASE_URL in .env. Run this on a machine that can reach Neon.

Usage:
  python src/load_to_db.py alerts   --dataset path/to/ukrainian-air-raid-sirens-dataset/datasets [--official] [--dry-run]
  python src/load_to_db.py messages [--dry-run]            # data/telegram/*.jsonl -> messages

Alerts source: github.com/Vadimkin/ukrainian-air-raid-sirens-dataset (volunteer data from eTryvoga: oblast level,
2022-02-25 .. today; official data: raion/hromada level since Dec 2025). All times UTC.
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def slug(region: str) -> str:
    s = region.lower().replace(" oblast", "").strip()
    return re.sub(r"[^a-z]+", "-", s).strip("-")


def iter_alerts(dataset: Path, official: bool):
    with (dataset / "volunteer_data_en.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            yield (slug(r["region"]), r["started_at"], r["finished_at"] or None,
                   "air_raid", "volunteer", r["naive"] == "True", "oblast")
    if official:
        with (dataset / "official_data_en.csv").open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                yield (slug(r["oblast"]), r["started_at"], r["finished_at"] or None,
                       "air_raid", "official", False, r["level"])


def connect():
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (see .env.example)")
    return psycopg.connect(url)


def cmd_alerts(a):
    rows = list(iter_alerts(Path(a.dataset), a.official))
    print(f"parsed {len(rows)} alert rows")
    if a.dry_run:
        print("sample:", rows[0], rows[-1])
        return
    sql = ("INSERT INTO alerts(region_id, ts_start, ts_end, alert_type, source, naive, level) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING")
    with connect() as conn, conn.cursor() as cur:
        for i in range(0, len(rows), 5000):
            cur.executemany(sql, rows[i:i + 5000])
            conn.commit()
            print(f"  {min(i + 5000, len(rows))}/{len(rows)}")


def cmd_messages(a):
    rows = []
    for p in sorted((ROOT / "data" / "telegram").glob("*.jsonl")):
        for line in p.open(encoding="utf-8"):
            m = json.loads(line)
            rows.append((m["id"], m["source_id"], m["ts_published"], "", m["text"], m["url"], m["hash"]))
    print(f"parsed {len(rows)} messages")
    if a.dry_run:
        return
    sql = ("INSERT INTO messages(id, source_id, ts_published, lang, text, url, hash) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING")
    with connect() as conn, conn.cursor() as cur:
        for i in range(0, len(rows), 2000):
            cur.executemany(sql, rows[i:i + 2000])
            conn.commit()
            print(f"  {min(i + 2000, len(rows))}/{len(rows)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("alerts")
    pa.add_argument("--dataset", required=True)
    pa.add_argument("--official", action="store_true", help="also load raion/hromada-level official data")
    pa.add_argument("--dry-run", action="store_true")
    pa.set_defaults(fn=cmd_alerts)
    pm = sub.add_parser("messages")
    pm.add_argument("--dry-run", action="store_true")
    pm.set_defaults(fn=cmd_messages)
    args = ap.parse_args()
    args.fn(args)
