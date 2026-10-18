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
NUMWORDS = {"одна": 1, "одну": 1, "один": 1, "одного": 1, "одной": 1, "две": 2, "два": 2, "двух": 2, "три": 3, "трех": 3, "трёх": 3, "четыре": 4, "четырех": 4,
            "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9, "десять": 10, "десяти": 10,
            "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
            "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "двадцати": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50}
NUM = r"(\d{1,4}|" + "|".join(sorted(NUMWORDS, key=len, reverse=True)) + r")"
MISSILE_UNIT = (r"(?:(?:крылат|управляем|оперативно-тактическ|противокорабельн|противорадиолокационн|зенитн|баллистическ|авиационн|реактивн|дальнобойн|американск|французск|британск|украинск|большой\s+дальности)\w*\s+){0,4}"
                r"(?:ракет\w*|снаряд\w*(?:\s+(?:РСЗО|системы\s+залпового\s+огня))?(?:\s+(?:«|\")?(?:HIMARS|Хаймарс|Химарс|Ольха|Вампир|Vampire))?|авиационн\w*\s+бомб\w*|авиабомб\w*)")
MISSILES = re.compile(NUM + r"\s+" + MISSILE_UNIT, re.I)
MISSILE_SINGULAR = re.compile(r"(?<![\d\w])(?:реактивный снаряд|крылатая ракета|управляемая авиационная бомба|баллистическая ракета|ракета)\b", re.I)
DRONES_W = re.compile(NUM + r"\s+(?:украинск\w+\s+)?(?:беспилотн|БПЛА|БпЛА)", re.I)
INTERCEPT = re.compile(r"(?:средствами (?:противовоздушной обороны|ПВО)|перехвачен\w*|сбит\w*|уничтожен\w*)[^\n]{0,500}", re.I)


def to_int(tok: str) -> int:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else NUMWORDS.get(tok, 0)


def parse(text: str):
    if not HEAD.search(text) or "украинск" not in text[:1500].lower():
        return None
    if re.search(r"(?:со|с) \d{1,2}(?: \w+)? по \d{1,2} \w+ \d{4}|на прошедшей неделе|за неделю", text[:400].lower()):
        return None  # weekly digests ("Сводка ... со 2 по 8 мая"); daily multi-part summaries ("Часть 2") stay
    m = DRONES.search(text[:700]) or DRONES2.search(text[:700])
    head = text[:900]
    mis = 0
    for sent in INTERCEPT.findall(text[:4000]):
        low = sent.lower()
        if "украинск" not in low and "бпла" not in low and "беспилот" not in low and "всу" not in low:
            continue
        if "с начала" in low or low.startswith("всего"):  # cumulative since-2022 totals, not the day's claim
            continue
        if re.search(r"за (?:прошедшую |текущую |минувшую )?неделю|за период|с \d{1,2} по \d{1,2}", low) or re.search(r"за (?:прошедшую |текущую |минувшую )?неделю|за период|с \d{1,2} по \d{1,2}", text[:300].lower()):
            continue  # weekly digests
        found = MISSILES.findall(sent)
        mis += sum(to_int(x) for x in found)
        if not found and MISSILE_SINGULAR.search(sent):
            mis += 1
    if not m:
        m = DRONES_W.search(head)
    if not m and not mis:
        return None
    return {"drones": to_int(m.group(1)) if m else 0, "missiles": mis}


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
