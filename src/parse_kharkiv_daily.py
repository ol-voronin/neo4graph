"""Parse the Kharkiv governor's daily summaries (@synegubov, 'Протягом минулої доби ...') into per-day weapon counts.

Gives OBLAST-level counts of what the enemy used on Kharkivshchyna in the previous ~24h, plus whether the city of
Kharkiv itself (and which districts) was attacked. It is NOT a per-city count: city-level massed strike is
approximated as (city attacked) AND (oblast count >= threshold). Keep that caveat in any analysis.

Usage: python src/parse_kharkiv_daily.py [--sql]
Input : data/telegram/synegubov*.jsonl   Output: data/kharkiv_daily.jsonl
"""
import argparse
import glob
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START = re.compile(r"Протягом минулої доби")
CITY_RE = re.compile(r"ворожих ударів зазнали\s+(м\.\s*Харків|[^.]*?м\.\s*Харків)", re.I)
SETTL_RE = re.compile(r"зазнали\s+(?:м\.\s*Харків\s+і\s+)?(\d+)\s+населен|зазнали\s+(\d+)\s+населен", re.I)
DISTR_RE = re.compile(r"Ворог атакував([^.\n]{0,200}?)\s+райони?\s+Харкова", re.I)  # bounded: nested quantifiers backtracked catastrophically
BLOCK_RE = re.compile(r"різні види озброєння:?(.*?)(?:Пошкоджено|➡️|🔻|$)", re.S)
LINE_RE = re.compile(r"▪️\s*(\d+)\s+([^;\n]+)")
KILLED_RE = re.compile(r"загинул[иаo]\s+(\d+|четверо|троє|двоє)|загинули\s+(\d+)", re.I)
WORDNUM = {"двоє": 2, "троє": 3, "четверо": 4}


def classify(label: str) -> str:
    l = label.lower()
    if "ракет" in l:
        return "missiles"
    if "каб" in l or "фаб" in l:
        return "guided_bombs"
    if "рсзв" in l or "рсзо" in l:
        return "mlrs"
    if "fpv" in l:
        return "fpv"
    if "бпла" in l or "дрон" in l:
        return "drones"
    return "other"


def districts(raw: str):
    raw = re.sub(r"(?i)\b(атакував|БпЛА|ракетами|ракетами|КАБ|ФАБ|і)\b", ",", raw)
    return sorted({x.strip(" ,") for x in re.split(r",", raw) if len(x.strip(" ,")) > 3})


def parse(text: str):
    if not START.search(text):
        return None
    out = {"missiles": 0, "guided_bombs": 0, "mlrs": 0, "fpv": 0, "drones": 0, "other": 0}
    blk = BLOCK_RE.search(text)
    for n, label in LINE_RE.findall(blk.group(1) if blk else ""):
        out[classify(label)] += int(n)
    head = text.split("\n", 1)[0]
    m = SETTL_RE.search(head)
    k = KILLED_RE.search(text)
    killed = None
    if k:
        g = k.group(1) or k.group(2)
        killed = WORDNUM.get(g.lower(), None) if not g.isdigit() else int(g)
    d = DISTR_RE.search(text)
    return {**out,
            "city_attacked": bool(re.search(r"м\.\s*Харків", head)),
            "settlements_n": int(m.group(1) or m.group(2)) if m else None,
            "districts_attacked": districts(d.group(1)) if d else [],
            "killed_first_mention": killed,
            "has_weapon_block": bool(blk)}


def main(sql=False):
    rows = {}
    for p in glob.glob(str(ROOT / "data/telegram/synegubov*.jsonl")):
        for line in open(p, encoding="utf-8"):
            m = json.loads(line)
            r = parse(m["text"])
            if not r:
                continue
            ts = datetime.fromisoformat(m["ts_published"].replace("Z", "+00:00"))
            day = (ts - timedelta(days=1)).date().isoformat()  # covers the previous day
            rows[day] = {"date": day, "posted_at": m["ts_published"], "msg_id": m["id"], "url": m["url"], **r}
    out = ROOT / "data" / "kharkiv_daily.jsonl"
    out.write_text("\n".join(json.dumps(v, ensure_ascii=False) for _, v in sorted(rows.items())) + "\n", encoding="utf-8")
    print(f"{len(rows)} daily summaries -> {out}")
    for _, v in sorted(rows.items()):
        print(v["date"], {k: v[k] for k in ("missiles", "guided_bombs", "drones", "fpv", "city_attacked", "districts_attacked")})
    if sql:
        for v in rows.values():
            print("INSERT INTO kharkiv_daily(date,msg_id,url,missiles,guided_bombs,mlrs,drones,fpv,city_attacked,districts,settlements_n) VALUES "
                  f"('{v['date']}','{v['msg_id']}','{v['url']}',{v['missiles']},{v['guided_bombs']},{v['mlrs']},{v['drones']},{v['fpv']},"
                  f"{str(v['city_attacked']).lower()},ARRAY[{','.join(repr(x) for x in v['districts_attacked'])}]::text[],{v['settlements_n'] or 'NULL'}) "
                  "ON CONFLICT (date) DO NOTHING;")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql", action="store_true")
    main(ap.parse_args().sql)
