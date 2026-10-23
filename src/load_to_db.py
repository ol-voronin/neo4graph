"""Load data into Postgres (Neon). Needs DATABASE_URL in .env. Run this on a machine that can reach Neon.

Usage:
  python src/load_to_db.py alerts   --dataset path/to/ukrainian-air-raid-sirens-dataset/datasets [--official] [--dry-run]
  python src/load_to_db.py messages [--dry-run]            # data/telegram/*.jsonl -> messages
  python src/load_to_db.py oblast   [--dry-run]            # data/oblast_daily.jsonl -> oblast_daily

Alerts source: github.com/Vadimkin/ukrainian-air-raid-sirens-dataset (volunteer data from eTryvoga: oblast level,
2022-02-25 .. today; official data: raion/hromada level since Dec 2025). All times UTC.
"""
import argparse
import csv
import json
from collections import Counter
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


def refresh_search_names(conn):
    """Rebuild the name index behind /api/search (db/migrations/013_search_trgm.sql); a no-op until it is applied."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('search_names') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY search_names")
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print("search_names refresh failed:", e)


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


# sources.type is checked in the database (official, state_media, milblogger, independent, analyst, osint, agency):
# collector configs use a few more names, mapped here so a new feed never breaks the whole load
TYPE_ALIAS = {"media": "independent", "regional_media": "independent", "news": "independent"}


def source_type(t):
    return TYPE_ALIAS.get(t, t) if t else "independent"


def channels():
    """Every channel entry (a dict with "name") anywhere in channels.json."""
    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("name"), str):
                yield x
            for v in x.values():
                yield from walk(v)
        elif isinstance(x, list):
            for v in x:
                yield from walk(v)
    seen = {}
    for c in walk(json.loads((ROOT / "channels.json").read_text(encoding="utf-8"))):
        seen.setdefault(c["name"], c)
    return list(seen.values())


def cmd_messages(a):
    rows = []
    for p in sorted((ROOT / "data" / "telegram").glob("*.jsonl")) + sorted((ROOT / "data" / "rss").glob("*.jsonl")):
        for line in p.open(encoding="utf-8"):
            m = json.loads(line)
            if not m.get("ts_published"):
                continue
            rows.append((m["id"], m["source_id"], m["ts_published"], "", m["text"], m["url"], m["hash"]))
    print(f"parsed {len(rows)} messages")
    if a.dry_run:
        return
    from collect_rss import SOURCES  # RSS sources register themselves; Telegram ones come from channels.json
    with connect() as conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO sources(id, name, country, lang, type, platform, tier) VALUES (%s,%s,%s,'en',%s,'web','B') ON CONFLICT (id) DO NOTHING",
                        [(f"rss:{k}", n, c, source_type(t)) for k, (n, c, t) in SOURCES.items()])
        cur.executemany("INSERT INTO sources(id, name, country, lang, type, platform, tier, region) VALUES (%s,%s,%s,%s,%s,'telegram',%s,%s) ON CONFLICT (id) DO NOTHING",
                        [(f"tg:{c['name']}", c.get("title") or c["name"], c.get("country"), {"RU": "ru", "UA": "uk"}.get(c.get("country"), "en"),
                          source_type(c.get("type")), c.get("tier") or "B", c.get("region")) for c in channels()])
        conn.commit()
        # a message of a source that is still not registered would fail its whole 2 000-row batch (foreign key): skip it
        cur.execute("SELECT id FROM sources")
        known = {r[0] for r in cur.fetchall()}
    skipped = Counter(r[1] for r in rows if r[1] not in known)
    rows = [r for r in rows if r[1] in known]
    if skipped:
        print("skipped (source not registered):", dict(skipped))
    sql = ("INSERT INTO messages(id, source_id, ts_published, lang, text, url, hash) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING")
    with connect() as conn, conn.cursor() as cur:
        for i in range(0, len(rows), 2000):
            cur.executemany(sql, rows[i:i + 2000])
            conn.commit()
            print(f"  {min(i + 2000, len(rows))}/{len(rows)}")


def cmd_waves(a):
    """Load data/waves_kpszsu.jsonl. Waves are versioned: changed figures create version+1, never overwrite."""
    path = ROOT / "data" / "waves_kpszsu.jsonl"
    waves = [json.loads(l) for l in path.open(encoding="utf-8")]
    print(f"parsed {len(waves)} waves")
    if a.dry_run:
        return
    cols = ["launched_drones", "launched_other", "launched_missiles", "shot_down_total", "hit_locations",
            "debris_locations", "missiles_unspecified"]
    new = changed = 0
    with connect() as conn, conn.cursor() as cur:
        for w in waves:
            vals = [w["launched_drones"], w["launched_other"], w["launched_missiles_counted"], w["shot_down_total"],
                    w["hit_locations"], w["debris_locations"], w["missiles_unspecified"]]
            cur.execute("SELECT version, " + ",".join(cols) + " FROM waves WHERE id=%s ORDER BY version DESC LIMIT 1",
                        (w["id"],))
            row = cur.fetchone()
            if row and list(row[1:]) == vals:
                continue
            version = (row[0] + 1) if row else 1
            changed += 1 if row else 0
            new += 0 if row else 1
            cur.execute(
                "INSERT INTO waves(id, version, date, kind, " + ",".join(cols) + ", source_url, source, precision, confidence) "
                "VALUES (%s,%s,%s,%s," + ",".join(["%s"] * len(cols)) + ",%s,'kpszsu','oblast',0.9)",
                (w["id"], version, w["date"], w["kind"], *vals, w["url"]))
        conn.commit()
    print(f"waves: {new} new, {changed} revised")


def cmd_kharkiv(a):
    """Load data/kharkiv_daily.jsonl (see parse_kharkiv_daily.py). Existing days are updated (figures can be corrected)."""
    rows = [json.loads(l) for l in (ROOT / "data" / "kharkiv_daily.jsonl").open(encoding="utf-8")]
    print(f"parsed {len(rows)} days")
    if a.dry_run:
        return
    sql = ("INSERT INTO kharkiv_daily(date,msg_id,url,missiles,guided_bombs,mlrs,drones,fpv,city_attacked,districts,settlements_n) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (date) DO UPDATE SET msg_id=EXCLUDED.msg_id, "
           "missiles=EXCLUDED.missiles, guided_bombs=EXCLUDED.guided_bombs, mlrs=EXCLUDED.mlrs, drones=EXCLUDED.drones, "
           "fpv=EXCLUDED.fpv, city_attacked=EXCLUDED.city_attacked, districts=EXCLUDED.districts, settlements_n=EXCLUDED.settlements_n")
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(sql, [(r["date"], r["msg_id"], r["url"], r["missiles"], r["guided_bombs"], r["mlrs"], r["drones"],
                               r["fpv"], r["city_attacked"], r["districts_attacked"], r["settlements_n"]) for r in rows])
        conn.commit()


OBLAST_COLS = ["region_id", "date", "source", "missiles", "drones", "fpv", "kab", "mlrs", "artillery", "attacks_n", "settlements_n",
               "shot_down", "killed", "injured", "attacked", "city_attacked", "complete", "msg_ids", "url", "parser"]


def cmd_oblast(a):
    """Load data/oblast_daily.jsonl (parse_oblast_daily.py) into oblast_daily (migration 015).
    Each cron run only sees the last few pages of a channel, so a day may be re-parsed from fewer posts: merge with MAX
    per count and OR per flag (same rule as parse_oblast_daily.merge) instead of overwriting."""
    path = ROOT / "data" / "oblast_daily.jsonl"
    rows = [json.loads(l) for l in path.open(encoding="utf-8")] if path.exists() else []
    print(f"parsed {len(rows)} oblast-days")
    if a.dry_run or not rows:
        return
    nums = ["missiles", "drones", "fpv", "kab", "mlrs", "artillery", "attacks_n", "settlements_n", "shot_down", "killed", "injured"]
    upd = ", ".join([f"{c}=GREATEST(oblast_daily.{c}, EXCLUDED.{c})" for c in nums]
                    + [f"{c}=oblast_daily.{c} OR EXCLUDED.{c}" for c in ("attacked", "complete")]
                    + ["city_attacked=CASE WHEN oblast_daily.city_attacked IS NULL THEN EXCLUDED.city_attacked "
                       "ELSE oblast_daily.city_attacked OR coalesce(EXCLUDED.city_attacked, false) END",
                       "msg_ids=ARRAY(SELECT DISTINCT unnest(oblast_daily.msg_ids || EXCLUDED.msg_ids) ORDER BY 1)",
                       "url=coalesce(oblast_daily.url, EXCLUDED.url)", "parser=EXCLUDED.parser", "updated_at=now()"])
    sql = (f"INSERT INTO oblast_daily({','.join(OBLAST_COLS)}) VALUES ({','.join(['%s'] * len(OBLAST_COLS))}) "
           f"ON CONFLICT (region_id, date, source) DO UPDATE SET {upd}")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('oblast_daily') IS NOT NULL")
        if not cur.fetchone()[0]:
            print("oblast_daily does not exist yet (apply db/migrations/015_oblast_daily.sql): skipping")
            return
        cur.executemany(sql, [tuple(r.get(c) for c in OBLAST_COLS) for r in rows])
        conn.commit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pw = sub.add_parser("waves")
    pw.add_argument("--dry-run", action="store_true")
    pw.set_defaults(fn=cmd_waves)
    pk = sub.add_parser("kharkiv")
    pk.add_argument("--dry-run", action="store_true")
    pk.set_defaults(fn=cmd_kharkiv)
    po = sub.add_parser("oblast")
    po.add_argument("--dry-run", action="store_true")
    po.set_defaults(fn=cmd_oblast)
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
