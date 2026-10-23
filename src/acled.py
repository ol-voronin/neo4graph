"""ACLED weekly aggregates -> Neon table acled_weekly (migration 026).

The owner downloads the myACLED "Aggregated data" export (Open access level: week x admin1 x sub_event_type, no single
events) and runs this locally. The data is licensed (no redistribution): it goes to the private database only, never
into the repo or the public lake release.

  python src/acled.py load "~/Desktop/ACLED Data_2026-10-23_....csv" [--countries Ukraine,Russia]
  python src/acled.py status

Strike classes used by the oblast model v3 (features.acled_weekly): 'Air/drone strike', 'Shelling/artillery/missile
attack' and 'Disrupted weapons use' (interceptions: a fully shot-down drone wave is recorded there, not as a strike).
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_to_db import connect  # noqa: E402

ADMIN1 = {"Cherkasy": "cherkaska", "Chernihiv": "chernihivska", "Chernivtsi": "chernivetska", "Crimea": "crimea",
          "Dnipropetrovsk": "dnipropetrovska", "Donetsk": "donetska", "Ivano-Frankivsk": "ivano-frankivska", "Kharkiv": "kharkivska",
          "Kherson": "khersonska", "Khmelnytskyi": "khmelnytska", "Kirovohrad": "kirovohradska", "Kyiv": "kyivska",
          "Kyiv City": "kyiv-city", "Luhansk": "luhanska", "Lviv": "lvivska", "Mykolaiv": "mykolaivska", "Odesa": "odeska",
          "Poltava": "poltavska", "Rivne": "rivnenska", "Sumy": "sumska", "Ternopil": "ternopilska", "Vinnytsia": "vinnytska",
          "Volyn": "volynska", "Zakarpattia": "zakarpatska", "Zaporizhia": "zaporizka", "Zhytomyr": "zhytomyrska"}
STRIKE_SUB = ("Air/drone strike", "Shelling/artillery/missile attack", "Disrupted weapons use")


def num(s):
    s = (s or "").replace(",", "").strip()
    return int(float(s)) if s else None


def load(a):
    countries = set(a.countries.split(","))
    rows = []
    with open(Path(a.csv).expanduser(), encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if r["country"] not in countries:
                continue
            rid = ADMIN1.get(r["admin1"]) if r["country"] == "Ukraine" else None
            if r["country"] == "Ukraine" and rid is None:
                sys.exit(f"unknown Ukrainian admin1 {r['admin1']!r}: add it to ADMIN1")
            rows.append((r["week"], r["country"], r["admin1"], rid, r["event_type"], r["sub_event_type"], num(r["events"]) or 0,
                         num(r["fatalities"]) or 0, num(r["population_exposure"]), r.get("disorder_type")))
    print(f"rows: {len(rows)} ({', '.join(sorted(countries))}), weeks {min(r[0] for r in rows)} .. {max(r[0] for r in rows)}")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE aw (LIKE acled_weekly INCLUDING DEFAULTS) ON COMMIT DROP")
        with cur.copy("COPY aw (week, country, admin1, region_id, event_type, sub_event_type, events, fatalities, population_exposure, disorder_type) FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)
        # a re-export replaces the weeks it covers for these countries (ACLED revises recent weeks)
        cur.execute("DELETE FROM acled_weekly w WHERE w.country = ANY(%s) AND w.week BETWEEN (SELECT min(week) FROM aw) AND (SELECT max(week) FROM aw)",
                    (sorted(countries),))
        cur.execute("INSERT INTO acled_weekly SELECT * FROM aw ON CONFLICT DO NOTHING")
        print("loaded", cur.rowcount)


def status(a):
    with connect() as conn:
        for r in conn.execute("SELECT country, count(*), min(week), max(week), max(loaded_at) FROM acled_weekly GROUP BY 1 ORDER BY 1"):
            print(*r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("load")
    p.add_argument("csv")
    p.add_argument("--countries", default="Ukraine,Russia")
    sub.add_parser("status")
    a = ap.parse_args()
    {"load": load, "status": status}[a.cmd](a)
