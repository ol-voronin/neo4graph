"""Parse Russian MoD (@mod_russia) air-defence reports into a daily series of Ukrainian attacks on Russia.

Reports look like: "В течение прошедшей ночи с 20.00 16 октября до 8.00 17 октября дежурными силами ПВО перехвачены и
уничтожены 610 украинских беспилотных летательных аппаратов самолетного типа над ..." (night, day and evening windows).
The windows are disjoint, so the day total = sum of all report numbers on that Moscow-time calendar date.
CAVEATS: this is the RUSSIAN claim of what it intercepted (a lower bound of what was launched, and a claim, not verified);
missiles are summed from any 'N ракет' mention in the same posts (Neptune, Storm Shadow, Flamingo, ...), so it is approximate.

Usage: python src/parse_mod_russia.py [--sql]   Input: data/telegram/mod_russia*.jsonl   Output: data/ru_attacks_daily.jsonl
Table (db/migrations/005_ru_attacks_daily.sql): ru_attacks_daily(date, drones, missiles, posts, source)
"""
import argparse
import glob
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEAD = re.compile(r"(?:перехвачен|уничтожен|сбит)\w*", re.I)
DRONES = re.compile(r"(?:перехвачен\w*\s+и\s+)?(?:уничтожен\w*|сбит\w*|перехвачен\w*)\s+(\d{1,5})\s+украинск\w+\s+беспилотн", re.I)
DRONES2 = re.compile(r"(\d{1,5})\s+украинск\w+\s+беспилотн", re.I)
MISSILES = re.compile(r"(\d{1,4})\s+(?:\w+\s+){0,2}ракет\w*", re.I)


def parse(text: str):
    if not HEAD.search(text) or "украинск" not in text[:600].lower():
        return None
    m = DRONES.search(text[:700]) or DRONES2.search(text[:700])
    mis = sum(int(x) for x in MISSILES.findall(text[:700]))
    if not m and not mis:
        return None
    return {"drones": int(m.group(1)) if m else 0, "missiles": mis}


def main(sql=False):
    day = defaultdict(lambda: {"drones": 0, "missiles": 0, "posts": 0})
    seen = set()
    for p in glob.glob(str(ROOT / "data/telegram/mod_russia*.jsonl")):
        for line in open(p, encoding="utf-8"):
            m = json.loads(line)
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            r = parse(m["text"])
            if not r:
                continue
            ts = datetime.fromisoformat(m["ts_published"].replace("Z", "+00:00")) + timedelta(hours=3)  # MSK
            d = ts.date().isoformat()
            day[d]["drones"] += r["drones"]; day[d]["missiles"] += r["missiles"]; day[d]["posts"] += 1
    rows = [{"date": d, **v} for d, v in sorted(day.items())]
    (ROOT / "data" / "ru_attacks_daily.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    print(f"{len(rows)} days -> data/ru_attacks_daily.jsonl")
    if sql:
        vals = ",".join(f"('{r['date']}',{r['drones']},{r['missiles']},{r['posts']})" for r in rows)
        print("INSERT INTO ru_attacks_daily(date,drones,missiles,posts) VALUES " + vals +
              " ON CONFLICT (date) DO UPDATE SET drones=EXCLUDED.drones, missiles=EXCLUDED.missiles, posts=EXCLUDED.posts;")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--sql", action="store_true")
    main(ap.parse_args().sql)
