"""ARMA: «Єдиний державний реєстр активів, на які накладено арешт у кримінальному провадженні» (data.gov.ua, ~740 MB JSON,
weekly) -> arma_assets, one row per asset with its court decisions (migration 024, docs/arma.md).

The file is one item per line and is not valid JSON as a whole (actionincomesum uses a decimal comma: 583526,51), so it is
read line by line and that field is fixed before json.loads. Several rows per asset id (one per decision / ARMA action) are
folded into one row: case numbers (docnum), ЄРДР numbers, a compact decision list, the latest ARMA state, valuation.

Kept: vehicles, real estate, land, shares / corporate rights, vessels, aircraft. Not stored row by row (Neon size budget):
money lots, railcars, seized goods (boxes, phones, documents, cards, laptops…) and anything unclassified — they are counted
in arma_counts only.

  python src/arma_assets.py [--file edra.json] [--dry-run]
"""
import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

URL = ("https://data.gov.ua/dataset/d6cc6909-4098-47bc-a2e4-96182f446982/resource/"
       "02435739-9481-40ca-b40f-6379b6ed5949/download/edra_opendata.json")
FIX = re.compile(r'("actionincomesum":-?\d+),(\d+)')
KEEP = ("vehicle", "real_estate", "land", "shares", "vessel", "aircraft")  # money (42 k cash / account lots, rarely named) -> counts only

# same stems as web/api/_text.js OBLASTS (slug, stems); Kyiv city before Kyiv oblast is decided below
OBLASTS = [
    ("kharkivska", "харк|харьк"), ("kyivska", "київськ|київщин|киевск"), ("dnipropetrovska", "дніпропетров|дніпр|кривий ріг|кривого рогу"),
    ("odeska", "одес"), ("zaporizka", "запор"), ("lvivska", "львів|львов"), ("sumska", "сумськ|сумщин|м\\. суми"), ("chernihivska", "черніг"),
    ("poltavska", "полтав|кременчу"), ("mykolaivska", "миколаїв"), ("khersonska", "херсон"), ("donetska", "донец|донеч|краматор|слов'янськ|маріупол"),
    ("luhanska", "луган"), ("vinnytska", "вінниц"), ("volynska", "волин|луцьк"), ("zhytomyrska", "житомир"), ("zakarpatska", "закарпат|ужгород"),
    ("ivano-frankivska", "франків|прикарпат"), ("kirovohradska", "кіровоград|кропивниц"), ("rivnenska", "рівнен|м\\. рівне"), ("ternopilska", "терноп"),
    ("khmelnytska", "хмельниц"), ("cherkaska", "черкас"), ("chernivetska", "чернівц|буковин"), ("crimea", "крим|севастопол|сімферопол"),
]
OBL_RE = [(slug, re.compile(stems)) for slug, stems in OBLASTS]
KYIV_CITY = re.compile(r"(м\.\s*київ|місто київ|міста києва|м\.\s*києва|київ,)")


def kind_of(desc, props):
    d, p = desc.lower(), props.lower()
    if "свідоцтво" in d or "посвідчення" in d or "документ" in d or d.startswith("рухоме майно") or "копія" in d[:40]:
        return "goods"
    if "повітряного судна" in p or re.search(r"\b(літак|гелікоптер|вертоліт)\b|повітрян\w* судн", d):
        return "aircraft"
    if "назва судна" in p or "тип і призначення судна" in p or re.search(r"\bсудн[оа]\b|яхт|катер|баржа|буксир", d):
        return "vessel"
    if re.search(r"вагон|цистерн|локомотив|тепловоз", d) or "вид вагону" in p:
        return "railcar"
    if re.search(r"земельн|кадастров|\*{10}:\*\*:\*{3}:\*{4}|\d{10}:\d\d:\d{3}:\d{4}", d) or re.match(r"площею [\d.,]+ га", d):
        return "land"
    if re.search(r"квартир|будин|нежитл|приміщен|нерухом|гараж|споруд|домоволодін|будівл|котедж|дачн", d):
        return "real_estate"
    if re.search(r"акці|статутн\w* капітал|корпоративн\w* прав|цінн\w* папер|частк\w* у", d):
        return "shares"
    if re.search(r"телефон|смартфон|ноутбук|системн\w* блок|планшет|картк|коробк|пакет", d):
        return "goods"
    if re.search(r"автомоб|транспортн\w* засіб|мотоцикл|причіп|трактор|автобус|комбайн|екскаватор|вантажн\w* авто|тягач|мопед|квадроцикл", d) \
            or ("марка" in p and "рік випуску" in p and not re.search(r"телефон|годинник|гіроскоп|прилад", d)):
        return "vehicle"
    if re.search(r"грошов|кошти|гроші|готівк|валют|депозит|банківськ\w* рахун", d) or "валюта" in p or p.startswith("сума"):
        return "money"
    return "goods"


def prop_map(props):
    out = {}
    for kv in (props or "").split("|"):
        if ":" in kv:
            k, v = kv.split(":", 1)
            out[k.strip().strip('"')] = v.strip()
    return out


def area_m2(desc, pm):
    s = pm.get("Площа") or ""
    m = re.search(r"([\d]+(?:[.,]\d+)?)\s*(га|кв\.?\s*м|м2|м²)?", s) if s else None
    if not m:
        m = re.search(r"площ\w*\s*(?:\(га\)\s*:?\s*)?([\d]+(?:[.,]\d+)?)\s*(га|кв\.?\s*м|м2|м²)", desc.lower())
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return round(v * 10000, 2) if (m.group(2) or "").startswith("га") or "(га)" in desc.lower() else v


def region_of(text):
    t = text.lower()
    if KYIV_CITY.search(t):
        return "kyiv-city"
    for slug, rx in OBL_RE:
        if rx.search(t):
            return slug
    return None


def dmy(s):
    try:
        return datetime.strptime((s or "").strip()[:10], "%d.%m.%Y").date()
    except ValueError:
        return None


def num(s):
    m = re.search(r"-?\d+(?:[.,]\d+)?", (s or "").replace(" ", ""))
    return float(m.group(0).replace(",", ".")) if m else None


def year_of(pm):
    y = num(pm.get("Рік випуску") or pm.get("Рік виготовлення"))
    return int(y) if y and 1900 <= y <= date.today().year + 1 else None


def items(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip().lstrip(",")
            if not s.startswith('{"id"'):
                continue
            if s.endswith("]}]}"):
                s = s[:-4]
            yield json.loads(FIX.sub(r"\1.\2", s))


def fold(path):
    """rows -> {asset_id: asset}, Counter of (kind, state) for every asset"""
    A = {}
    for it in items(path):
        aid = it["id"]
        a = A.get(aid)
        if a is None:
            desc, props = (it.get("description") or "").strip(), (it.get("props") or "").strip()
            a = A[aid] = {"asset_id": aid, "description": desc, "props": props, "kind": kind_of(desc, props),
                          "docnums": set(), "erdr": set(), "decisions": {}, "state": None, "state_at": None, "valuation": None, "income": None}
        for n in re.split(r"[,;]", it.get("docnum") or ""):
            n = n.strip().lower()
            if n:
                a["docnums"].add(n)
        for e in re.findall(r"\d{17}", it.get("numerdr") or ""):
            a["erdr"].add(e)
        key = (it.get("dectypename"), it.get("decisiondate"), it.get("docnum"))
        if key[0] or key[1]:
            a["decisions"][key] = [key[0], key[1], key[2]]
        if it.get("state"):
            at = dmy(it.get("dateaction"))
            if a["state_at"] is None or (at and at >= a["state_at"]):
                a["state"], a["state_at"] = it["state"], at
        v = re.search(r"Оціночна вартість майна \(грн\):\s*([\d\s.,]+)", it.get("props_2") or "")
        if v:
            a["valuation"] = max(a["valuation"] or 0, num(v.group(1)) or 0) or None
        inc = it.get("actionincomesum")
        if isinstance(inc, (int, float)) and inc:
            a["income"] = max(a["income"] or 0, inc)
    return A


# latest court decision type -> short code (the full text repeats on 117 k rows); several types joined by «;» keep the first
# first match wins: a decision that lifts and re-imposes in one go reads as an arrest
DEC_CODE = [("стягнення в дохід", "forfeited"), ("скасування рішення", "management_cancelled"), ("передача для реалізації", "sale"),
            ("передача майна в управління", "management"), ("накладення арешту", "arrest"), ("скасування арешту в частині", "lifted_partly"),
            ("арешт в частині", "arrest_partly"), ("скасування арешту", "lifted")]


def dec_code(t):
    t = (t or "").lower()
    return next((c for k, c in DEC_CODE if k in t), "other" if t else None)


def decs_of(a):
    return sorted(a["decisions"].values(), key=lambda x: dmy(x[1]) or date.min)


def lifted(a):
    """the latest court decision on the asset lifted the arrest (and nothing re-imposed it the same day or later)"""
    d = decs_of(a)
    return bool(d) and "скасування арешту майна" in (d[-1][0] or "") and "накладення арешту" not in (d[-1][0] or "")


def row(a, units=1):
    pm = prop_map(a["props"])
    decs = decs_of(a)
    dates = [dmy(x[1]) for x in decs if dmy(x[1])]
    brand, model, year = pm.get("Марка") or pm.get("Виробник") or None, pm.get("Модель") or pm.get("Модель повітряного судна") or None, year_of(pm)
    area = area_m2(a["description"], pm) if a["kind"] in ("real_estate", "land") else None
    structured = brand or model or year or area
    return (a["asset_id"], a["description"][:120], None if structured else (a["props"][:120] or None), a["kind"], brand and brand[:60], model and model[:60], year,
            area, region_of(a["description"]), sorted(a["docnums"]), sorted(a["erdr"]),
            dec_code(decs[-1][0]) if decs else None, a["state"], a["valuation"], a["income"], min(dates) if dates else None, max(dates) if dates else None, units)


def lots(keep):
    """identical lots (same description, props and case numbers: «40 гіроскопів», a fleet seized together) -> one row, units = n"""
    g = {}
    for a in keep:
        g.setdefault((a["description"], a["props"], tuple(sorted(a["docnums"]))), []).append(a)
    return [(min(v, key=lambda x: x["asset_id"]), len(v)) for v in g.values()]


COLS = ("asset_id, description, props, kind, brand, model, year, area, region, docnums, erdr, last_action, state, valuation, income, "
        "first_decision, last_decision, units")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "data" / "arma" / "edra.json"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = Path(a.file)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-sfL", "--retry", "3", "-A", "Mozilla/5.0", "-o", str(path), URL], check=True)
    A = fold(path)
    by = Counter(x["kind"] for x in A.values())
    cand = [x for x in A.values() if x["kind"] in KEEP]
    keep = [x for x in cand if not lifted(x)]
    rows = [row(x, n) for x, n in lots(keep)]
    print(f"assets {len(A)}; by kind {dict(by.most_common())}")
    print(f"  of {len(cand)} kept-kind assets: {len(cand) - len(keep)} with the arrest lifted dropped; {len(rows)} rows after folding identical lots")
    print(f"  rows with docnums {sum(bool(r[9]) for r in rows)}, with ЄРДР {sum(bool(r[10]) for r in rows)}; by kind {dict(Counter(r[3] for r in rows).most_common())}")
    if a.dry_run:
        for k in KEEP:
            for r in [r for r in rows if r[3] == k][:2]:
                print(k, "|", r[1][:90], "|", r[2], "|", r[4], r[5], r[6], r[7], r[8], "|", r[9][:3], r[10][:2], "|", r[11], r[12], r[13], r[16], "units", r[17])
        print("approx bytes", sum(len(str(r)) for r in rows))
        return
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute(f"CREATE TEMP TABLE aa AS SELECT {COLS} FROM arma_assets WITH NO DATA")
    with cur.copy(f"COPY aa ({COLS}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
    if len(rows) < 0.5 * (cur.execute("SELECT count(*) FROM arma_assets").fetchone()[0] or 0):
        sys.exit(f"refusing to replace arma_assets with {len(rows)} rows (less than half of the stored ones): format change?")
    cur.execute("TRUNCATE arma_assets")
    cur.execute(f"INSERT INTO arma_assets ({COLS}) SELECT {COLS} FROM aa")
    cur.execute("TRUNCATE arma_counts")
    cnt = Counter((x["kind"] if x["kind"] in KEEP and not lifted(x) else x["kind"] + ("_lifted" if x["kind"] in KEEP else ""), x["state"] or "") for x in A.values())
    with cur.copy("COPY arma_counts (kind, state, n) FROM STDIN") as cp:
        for (k, s), n in cnt.items():
            cp.write_row((k, s or None, n))
    conn.commit()
    print(f"written {len(rows)} assets, {len(cnt)} count rows")


if __name__ == "__main__":
    main()
