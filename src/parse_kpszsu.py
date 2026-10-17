"""Parse Air Force of Ukraine (kpszsu) summary posts into wave records.

Input : data/telegram/kpszsu_summaries.jsonl (posts containing 'ЗБИТО/ПОДАВЛЕНО' or 'Протягом дня')
Output: data/waves_kpszsu.jsonl  and (with --sql) INSERT statements for table `waves`.

What the posts contain (nationwide totals only):
  launched drones / missiles / S-8000 'Banderol', shot-down total, number of hit locations, debris locations.
What they do NOT contain: per-city counts. The 'single city: 7-10 missiles / 20 drones' definition therefore
cannot be computed from these posts alone (see docs/ground_truth.md).
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONTHS = {"січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6, "липня": 7,
          "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12}

NIGHT_RE = re.compile(r"У ніч на (\d{1,2}) (\w+)")
DAY_RE = re.compile(r"Протягом дня (\d{1,2}) (\w+)")
DRONES_RE = re.compile(r"(\d+)\s+ударн\w+\s+БпЛА")
OTHER_RE = re.compile(r"(\d+)\s+S-8000")
MISSILE_RE = re.compile(r"(\d+)\s+(?:балістичн|крилат|аеробалістичн|зенітн|керован|гіперзвуков|протирадіолокаційн|авіаційн)\w*\s+(?:[\w\-/\"“”«»]+\s+){0,1}ракет\w*")
TOTAL_RE = re.compile(r"Усього[^\n]*?(\d+)\s+ракет\w*[^\n]*?(\d+)\s+БпЛА")
DIRECT_MISSILE_RE = re.compile(r"(\d+)\s+ракет\w*")
DOWN_RE = re.compile(r"збито/подавлено\s+(\d+)", re.I)
HIT_RE = re.compile(r"влучання[^.\n]*?на\s+(\d+)\s+локаці", re.I)
DEBRIS_RE = re.compile(r"падіння[^.\n]*?на\s+(\d+)\s+локаці", re.I)


def parse_post(post: dict):
    text = post["text"]
    ts = datetime.fromisoformat(post["ts_published"].replace("Z", "+00:00"))
    m = NIGHT_RE.search(text)
    kind = "night"
    if not m:
        m = DAY_RE.search(text)
        kind = "day"
    if not m or m.group(2) not in MONTHS:
        return None
    day, month = int(m.group(1)), MONTHS[m.group(2)]
    year = ts.year - (1 if month > ts.month + 1 else 0)
    date = f"{year:04d}-{month:02d}-{day:02d}"
    # attack sentence = text before the "shot down" paragraph
    attack = text.split("💥")[0]
    attack_body = attack.split("противник атакував", 1)[-1]
    drones = DRONES_RE.search(attack_body)
    other = OTHER_RE.search(attack_body)
    total = TOTAL_RE.search(attack)
    if total:  # "Усього ... зафіксовано N засобів - X ракет і Y БпЛА": authoritative, avoid double counting
        missiles = int(total.group(1))
        if not drones:
            drones = type("M", (), {"group": lambda self, i, v=total.group(2): v})()
    else:
        missiles = sum(int(x) for x in MISSILE_RE.findall(attack_body))
        if not missiles:
            direct = DIRECT_MISSILE_RE.findall(attack_body)
            missiles = int(direct[0]) if direct else 0
    # bullet lines that mention missiles without any number -> counts are incomplete
    unnumbered = any((ln.lstrip().startswith("-") and "ракет" in ln and not re.search(r"\d", ln)) for ln in attack_body.split("\n")) if not total else False
    down = DOWN_RE.search(text)
    hit = HIT_RE.search(text)
    debris = DEBRIS_RE.search(text)
    return {
        "id": f"kpszsu:{date}:{kind}",
        "date": date,
        "kind": kind,
        "post_ts": post["ts_published"],
        "url": post["url"],
        "launched_drones": int(drones.group(1)) if drones else None,
        "launched_other": int(other.group(1)) if other else None,
        "launched_missiles_counted": missiles or None,  # only missiles with an explicit number in the post
        "missiles_unspecified": (bool(re.search(r"ракет(?!ні)", attack_body)) and not missiles) or unnumbered,
        "shot_down_total": int(down.group(1)) if down else None,
        "hit_locations": int(hit.group(1)) if hit else None,
        "debris_locations": int(debris.group(1)) if debris else None,
    }


def sql_val(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "data/telegram/kpszsu_summaries.jsonl"))
    ap.add_argument("--sql", action="store_true")
    a = ap.parse_args()
    out = {}
    for line in open(a.src, encoding="utf-8"):
        w = parse_post(json.loads(line))
        if w:
            out[w["id"]] = w  # later post (final numbers) wins
    waves = sorted(out.values(), key=lambda w: (w["date"], w["kind"]))
    (ROOT / "data").mkdir(exist_ok=True)
    with open(ROOT / "data/waves_kpszsu.jsonl", "w", encoding="utf-8") as f:
        for w in waves:
            f.write(json.dumps(w, ensure_ascii=False) + "\n")
    print(f"{len(waves)} waves parsed")
    if a.sql:
        cols = ["id", "date", "kind", "launched_drones", "launched_other", "launched_missiles", "shot_down_total",
                "hit_locations", "debris_locations", "missiles_unspecified", "source_url"]
        rows = []
        for w in waves:
            vals = [w["id"], w["date"], w["kind"], w["launched_drones"], w["launched_other"],
                    w["launched_missiles_counted"], w["shot_down_total"], w["hit_locations"],
                    w["debris_locations"], w["missiles_unspecified"], w["url"]]
            rows.append("(" + ",".join(sql_val(v) for v in vals) + ")")
        print("INSERT INTO waves(id,date,kind,launched_drones,launched_other,launched_missiles,shot_down_total,"
              "hit_locations,debris_locations,missiles_unspecified,source_url) VALUES\n" + ",\n".join(rows) +
              "\nON CONFLICT DO NOTHING;")
