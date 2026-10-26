"""Open data of the map.ukrainealarm.com page into Neon (db/migrations/037_ukrainealarm.sql). Runs on the VPS.

  python src/collect_ukrainealarm.py targets [--days 2]   # tracked targets per day / type / hour / region
  python src/collect_ukrainealarm.py alarms [--all]       # alarms with level + reason, per district

targets: /api/data/targetHistory?date=D. The page keeps ~7 days, so today and yesterday are re-fetched each run and the
  rows overwritten (the source revises a day while it lasts). --days 8 backfills what is still there.
alarms: one /api/v2/data/mapUpdate poll; only when the set of active alerts changed since the previous poll (state in
  data/ukrainealarm_state.json) or with --all, the 26 per-oblast /api/data/lastAlerts/<id> lists (last 20 alarms each)
  are fetched and upserted. Quiet hours cost one request per run.
The live target tracks of the map are encrypted by the site and are not read.
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_to_db import connect  # noqa: E402
from oblasts import OBLASTS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://map.ukrainealarm.com"
HDR = {"User-Agent": "Mozilla/5.0 (rhetoric-strikes research; once per run)", "Referer": BASE + "/",
       "Accept": "application/json"}
STATE = ROOT / "data" / "ukrainealarm_state.json"
KYIV = ZoneInfo("Europe/Kyiv")
# map region id -> name, the 26 oblast-level regions of the map (regions-standard-v1.geojson, rank 1)
MAP_OBLASTS = {3: "Хмельницька область", 4: "Вінницька область", 5: "Рівненська область", 8: "Волинська область",
               9: "Дніпропетровська область", 10: "Житомирська область", 11: "Закарпатська область",
               12: "Запорізька область", 13: "Івано-Франківська область", 14: "Київська область",
               15: "Кіровоградська область", 16: "Луганська область", 17: "Миколаївська область",
               18: "Одеська область", 19: "Полтавська область", 20: "Сумська область", 21: "Тернопільська область",
               22: "Харківська область", 23: "Херсонська область", 24: "Черкаська область",
               25: "Чернігівська область", 26: "Чернівецька область", 27: "Львівська область",
               28: "Донецька область", 31: "м. Київ", 9999: "Автономна Республіка Крим"}
BY_NAME = {v["name"] + " область": k for k, v in OBLASTS.items()} | {"м. Київ": "kyiv-city"}
SLUG = {str(i): BY_NAME.get(n) for i, n in MAP_OBLASTS.items()}


def get(path):
    r = requests.get(BASE + path, headers=HDR, timeout=30)
    r.raise_for_status()
    return r.json()


def targets(a):
    today = datetime.now(KYIV).date()
    with connect() as conn, conn.cursor() as cur:
        for k in range(a.days):
            d = today - timedelta(days=k)
            j = get(f"/api/data/targetHistory?date={d}")
            if not j.get("total") and not j.get("regions"):
                print(f"{d}: no targets (not kept any more, or none yet)")
                continue
            cur.execute("INSERT INTO ua_targets_day(d,total,types,hours,tracking,generated_at) VALUES (%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (d) DO UPDATE SET total=EXCLUDED.total, types=EXCLUDED.types, hours=EXCLUDED.hours, "
                        "tracking=EXCLUDED.tracking, generated_at=EXCLUDED.generated_at, fetched_at=now()",
                        (d, j["total"], json.dumps({t["key"]: t["total"] for t in j.get("types", [])}),
                         json.dumps(j.get("hours", [])), json.dumps(j.get("tracking")), j.get("generatedAt")))
            cur.execute("DELETE FROM ua_targets_region WHERE d=%s", (d,))
            cur.executemany("INSERT INTO ua_targets_region(d,ua_region_id,region_id,name,total,tracks) VALUES (%s,%s,%s,%s,%s,%s)",
                            [(d, str(r["regionId"]), SLUG.get(str(r["regionId"])), r["name"], r["total"], r.get("tracks"))
                             for r in j.get("regions", [])])
            conn.commit()
            types = ", ".join(f"{t['key']} {t['total']}" for t in j.get("types", []) if t["total"])
            print(f"{d}: {j['total']} targets ({types}); {len(j.get('regions', []))} regions")
            time.sleep(1)


def ts(s):
    return None if not s or s.startswith("0001-") else s


def alarms(a):
    upd = get("/api/v2/data/mapUpdate")
    active = sorted((x["regionId"], al["type"], lv["alertLevel"], lv.get("reason", ""))
                    for x in upd.get("alerts", []) for al in x.get("activeAlerts", []) for lv in al.get("activeAlertLevels", []))
    sig = hashlib.sha1(json.dumps(active).encode()).hexdigest()
    try:
        prev = json.loads(STATE.read_text()).get("sig")
    except (OSError, ValueError):
        prev = None
    print(f"active alert levels: {len(active)}")
    if sig == prev and not a.all:
        print("no change since the previous poll")
        return
    rows = []
    for oid in MAP_OBLASTS:
        try:
            lst = get(f"/api/data/lastAlerts/{oid}")
        except requests.RequestException as e:
            print(f"lastAlerts {oid}: {e}")
            continue
        for reg in lst if isinstance(lst, list) else []:
            for x in reg.get("alarms", []):
                par = str(x.get("parentRegionId") or reg.get("regionId") or oid)
                rows.append((x["id"], str(x["regionId"]), par, SLUG.get(par) or SLUG.get(str(oid)), x.get("regionName"),
                             x["startDate"], ts(x.get("endDate")), x.get("alertType"), x.get("alertLevel"),
                             x.get("reason") or None))
        time.sleep(0.4)
    with connect() as conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO ua_alarms(id,ua_region_id,ua_oblast_id,region_id,region_name,ts_start,ts_end,alert_type,level,reason) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET ts_end=EXCLUDED.ts_end, "
                        "level=EXCLUDED.level, reason=EXCLUDED.reason, fetched_at=now()", rows)
        conn.commit()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"sig": sig, "at": datetime.now(timezone.utc).isoformat()}))
    print(f"alarms upserted: {len(rows)} ({sum(r[6] is None for r in rows)} still on)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("targets")
    t.add_argument("--days", type=int, default=2)
    al = sub.add_parser("alarms")
    al.add_argument("--all", action="store_true")
    a = ap.parse_args()
    {"targets": targets, "alarms": alarms}[a.cmd](a)


if __name__ == "__main__":
    main()
