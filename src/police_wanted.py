"""Національна поліція open data from data.gov.ua (migration 021):
  wanted   — «Інформація про осіб, які переховуються від органів влади» (PersonsWanted.json, ~75 k records, daily): every
             record into wanted_persons with first_seen / last_seen; records gone from the file get removed = today;
             a per-day snapshot of totals into wanted_daily.
  missing  — «Інформація про осіб, зниклих безвісти» (BezvistiWanted.json): counts only (month, oblast, sex, age band)
             into missing_monthly; names and appearance are not stored.
Photos (several GB) are not downloaded.

  python src/police_wanted.py [wanted] [missing] [--file PATH] [--dry-run]
"""
import argparse
import io
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

URL = {
    "wanted": "https://data.gov.ua/dataset/5f3dbea6-9c56-4c69-ae62-e2eba13f01f8/resource/50dcaf3c-7550-46ce-8e84-a91875299518/download/personswanted.json",
    "missing": "https://data.gov.ua/dataset/206741b9-2dc1-4aff-bbfa-c0a3fd254447/resource/af13068e-6aa2-4c4f-a9b3-e76328ba4a01/download/bezvistiwanted.json",
}
# Oblast stems (locative / adjective forms as written by the police) -> alert-map slugs used across the project
OBLAST = [("вінниц", "vinnytska"), ("волин", "volynska"), ("дніпропетров", "dnipropetrovska"), ("донец", "donetska"),
          ("житомир", "zhytomyrska"), ("закарпат", "zakarpatska"), ("запоріз", "zaporizka"), ("івано-франків", "ivano-frankivska"),
          ("кіровоград", "kirovohradska"), ("луган", "luhanska"), ("львів", "lvivska"), ("миколаїв", "mykolaivska"),
          ("одес", "odeska"), ("полтав", "poltavska"), ("рівнен", "rivnenska"), ("сумськ", "sumska"), ("сумщин", "sumska"), ("тернопіл", "ternopilska"),
          ("харків", "kharkivska"), ("херсон", "khersonska"), ("хмельниц", "khmelnytska"), ("черкас", "cherkaska"),
          ("чернівец", "chernivetska"), ("чернігів", "chernihivska"), ("автономн", "crimea"), ("крим", "crimea"), ("крым", "crimea"), ("севастопол", "crimea"), ("російська федерація", "russia")]
KYIV_CITY = re.compile(r"(М\.\s*КИ[ЄЇ]В|У М\. КИЄВІ|ГУНП У М\.?\s*КИ)", re.I)
# Criminal Code article -> group. war_collab: treason, collaboration, aiding the aggressor, encroachment, terrorism, war crimes;
# military_evasion: evading mobilisation and military service, AWOL, desertion; border: illegal crossing / smuggling of people.
GROUPS = {
    "war_collab": {"109", "110", "110-2", "111", "111-1", "111-2", "113", "114", "114-1", "114-2", "258", "258-3", "258-5", "260", "436", "436-2", "437", "438"},
    "military_evasion": {"335", "336", "336-1", "402", "403", "404", "405", "406", "407", "408", "409", "410"},
    "border": {"332", "332-2"},
    "corruption": {"191", "210", "354", "364", "364-1", "365-2", "366", "366-2", "368", "368-2", "368-3", "368-4", "369", "369-2", "209"},
    "violent": {"115", "116", "118", "119", "121", "122", "152", "153", "187", "289", "296"},
    "property": {"185", "186", "189", "190", "192", "194"},
}
ART = {a: g for g, s in GROUPS.items() for a in s}


def fetch(kind, path=None):
    if path:
        return json.load(io.open(path, encoding="utf-8"))
    req = urllib.request.Request(URL[kind], headers={"User-Agent": "Mozilla/5.0 (rhetoric-graph open-data loader)"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(io.TextIOWrapper(r, encoding="utf-8"))


def region_of(*texts):
    """First text that names an oblast wins (ovd, then lost_place). Oblasts are tested before Kyiv because district names
    repeat city names («КИЇВСЬКИЙ РВ СІМФЕРОПОЛЬСЬКОГО МВ … В АР КРИМ» is Crimea)."""
    for t in texts:
        low = (t or "").lower()
        for stem, slug in OBLAST:
            if stem in low:
                return slug
        if KYIV_CITY.search(t or "") or "м. київ" in low or "києві" in low:
            return "kyiv-city"
        if "київськ" in low:
            return "kyivska"
    return "unknown"


def agency_of(ovd):
    o = (ovd or "").upper()
    if "СБУ" in o or "СЛУЖБИ БЕЗПЕКИ" in o:
        return "sbu"
    if "БЮРО РОЗСЛІДУВАНЬ" in o or "ДЕРЖАВНОГО БЮРО" in o or "ДБР" in o:
        return "dbr"
    if "ПРИКОРДОНН" in o:
        return "border_guard"
    if "НАБУ" in o or "АНТИКОРУПЦІЙН" in o:
        return "nabu"
    if re.search(r"ПОЛІЦІ|ГУНП|ДВБ|\bВП\b|МВС|ГУМВС|\bРВ\b|\bМВ\b|\bМРВ\b|\bГВ\b|ВНУТРІШНЬОЇ БЕЗПЕКИ|СТРАТЕГІЧНИХ РОЗСЛІДУВАНЬ|ВІДДІЛ|ВІДДІЛЕННЯ", o):
        return "police"
    return "other"


def article_of(s):
    m = re.search(r"СТ\.?\s*(\d{2,3}(?:-\d)?)", (s or "").upper())
    return m.group(1) if m else None


def day(s):
    try:
        d = date.fromisoformat((s or "")[:10])
        return d if date(1900, 1, 1) <= d <= date.today() else None
    except ValueError:
        return None


def rows_wanted(data):
    for x in data:
        a = article_of(x.get("article_crim"))
        yield (x["id"], x.get("category"), x.get("last_name_u"), x.get("first_name_u"), x.get("middle_name_u"), x.get("last_name_r"),
               x.get("first_name_r"), x.get("middle_name_r"), x.get("last_name_e"), x.get("first_name_e"), x.get("middle_name_e"),
               day(x.get("birth_date")), x.get("sex"), day(x.get("lost_date")), x.get("lost_place"), x.get("ovd"), agency_of(x.get("ovd")),
               region_of(x.get("ovd"), x.get("lost_place")), x.get("article_crim"), a, ART.get(a, "other" if a else "no_article"), x.get("restraint"))


COLS = ("id, category, last_name_u, first_name_u, middle_name_u, last_name_r, first_name_r, middle_name_r, last_name_e, first_name_e, "
        "middle_name_e, birth_date, sex, lost_date, lost_place, ovd, agency, region, article_crim, article, art_group, restraint")


def load_wanted(conn, data, dry):
    rows = list({r[0]: r for r in rows_wanted(data)}.values())  # ids are unique in practice; keep the last if not
    by = Counter(r[20] for r in rows)
    print(f"wanted: {len(rows)} records; by group {dict(by.most_common())}")
    print("  by agency", dict(Counter(r[16] for r in rows).most_common()), "unknown region", sum(r[17] == "unknown" for r in rows))
    if dry:
        return
    cur = conn.cursor()
    cur.execute(f"CREATE TEMP TABLE w AS SELECT {COLS} FROM wanted_persons WITH NO DATA")
    with cur.copy(f"COPY w ({COLS}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS.split(", ")[1:])
    cur.execute(f"INSERT INTO wanted_persons ({COLS}) SELECT {COLS} FROM w ON CONFLICT (id) DO UPDATE SET {sets}, last_seen = current_date, removed = NULL")
    cur.execute("UPDATE wanted_persons SET removed = current_date WHERE removed IS NULL AND NOT EXISTS (SELECT 1 FROM w WHERE w.id = wanted_persons.id)")
    gone = cur.rowcount
    cur.execute("""INSERT INTO wanted_daily (date, art_group, region, active, added, removed)
                   SELECT current_date, art_group, region, count(*) FILTER (WHERE removed IS NULL),
                          count(*) FILTER (WHERE first_seen = current_date), count(*) FILTER (WHERE removed = current_date)
                   FROM wanted_persons GROUP BY 2, 3
                   ON CONFLICT (date, art_group, region) DO UPDATE SET active = EXCLUDED.active, added = EXCLUDED.added, removed = EXCLUDED.removed""")
    conn.commit()
    print(f"  upserted {len(rows)}, newly removed {gone}")


def age_band(b, lost):
    if not b or not lost:
        return "?"
    a = (lost - b).days // 365
    return "<18" if a < 18 else "18-24" if a < 25 else "25-59" if a < 60 else "60+"


def load_missing(conn, data, dry):
    c = Counter()
    for x in data:
        lost = day(x.get("lost_date"))
        if not lost:
            continue
        sex = {"ЧОЛОВІЧА": "m", "ЖІНОЧА": "f"}.get((x.get("sex") or "").strip().upper(), "?")
        c[(lost.replace(day=1), region_of(x.get("ovd"), x.get("lost_place")), sex, age_band(day(x.get("birth_date")), lost))] += 1
    print(f"missing: {len(data)} records -> {len(c)} cells; since 2022-02: {sum(n for k, n in c.items() if k[0] >= date(2022, 2, 1))}")
    if dry:
        return
    cur = conn.cursor()
    cur.execute("DELETE FROM missing_monthly WHERE snapshot = current_date")
    with cur.copy("COPY missing_monthly (snapshot, month, region, sex, age_band, n) FROM STDIN") as cp:
        for (m, reg, sex, ab), n in c.items():
            cp.write_row((date.today(), m, reg, sex, ab, n))
    cur.execute("DELETE FROM missing_monthly WHERE snapshot < current_date - 35")  # keep ~a month of snapshots for revisions
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="*", default=["wanted", "missing"], choices=["wanted", "missing"])
    ap.add_argument("--file", help="local JSON instead of downloading (one kind only)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    conn = None
    if not a.dry_run:
        from load_to_db import connect
        conn = connect()
    for kind in a.what:
        data = fetch(kind, a.file)
        (load_wanted if kind == "wanted" else load_missing)(conn, data, a.dry_run)


if __name__ == "__main__":
    main()
