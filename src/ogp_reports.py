"""Office of the Prosecutor General (Офіс Генерального прокурора) report forms from data.gov.ua -> ogp_stats (docs/ogp_reports.md).

The forms are published as normalized JSON: cells (table, row labels level1..5, metric) and reports (one per period, values
by cell id). Layouts changed over the years, so one quantity has different cell ids in different reports; rows are matched
by their label instead (Criminal Code article at the end of the row label, section names, totals). National figures only:
the open JSON has no region breakdown. Values are cumulative from January (січень–місяць); view ogp_monthly derives the
month's increment.

  python src/ogp_reports.py [--forms forma_1,forma_2,forma_1_oz,forma_1_lft] [--dir data/ogp] [--dry-run]
Needs: ijson (streaming parse; forma_1/forma_2 are 100+ MB), psycopg.
"""
import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

D = "https://data.gov.ua/dataset"
FORMS = {
    "forma_1": f"{D}/a3fb85ad-56dc-496e-921a-9bc72f3a2e7a/resource/af7400bc-c050-40b3-b8cf-f1693edb5f62/download/forma_1.json",
    "forma_2": f"{D}/09ac0204-8dde-4124-8f90-eac8f6952474/resource/8d425a9d-14ec-4577-9ac1-a3a29b8e8f9a/download/forma_2.json",
    "forma_1_oz": f"{D}/e523c258-f5be-48da-a2c3-0b17926c0987/resource/9061e919-a37e-401f-9705-4f852e6f5100/download/forma_1_oz.json",
    "forma_1_lft": f"{D}/5ab13f1f-5179-443e-8cd1-377c7bee7513/resource/ce3622e0-4d54-4113-a655-4c9724005aa5/download/forma_1_lft.json",
}
# a row label that ends with a Criminal Code article: "Дезертирство, ст.408", "Колабораційна діяльність, ст. 111-1"
ART = re.compile(r"ст\.\s*(\d{2,3}(?:-\d)?)\s*$")
# Articles kept for every measure (the rest: forma_1 'registered' only), to keep the table near 10 MB.
# War and national security; corruption and legalisation (the fact-check topics in docs/factcheck.md).
WAR = {"109", "110", "110-2", "111", "111-1", "111-2", "113", "114", "114-1", "114-2", "258", "258-3", "258-5", "260", "332",
       "333", "336", "336-1", "337", "436", "436-1", "436-2", "437", "438", "442"} | {str(a) for a in range(402, 436)}
CORRUPTION = {"191", "206-2", "209", "209-1", "210", "211", "354", "364", "364-1", "365-2", "366", "366-2", "366-3", "368",
              "368-2", "368-3", "368-4", "368-5", "369", "369-2", "410"}
CURATED = WAR | CORRUPTION
SECTIONS = {  # Criminal Code sections (розділи), matched in section rows or in the subtable title of a "Усього" row
    "national_security": "основ національної безпеки",
    "peace_war": "миру, безпеки людства",
    "military": "встановленого порядку несення військової служби",
    "official": "у сфері службової діяльності",
    "economic": "у сфері господарської діяльності",
    "property": "проти власності",
    "justice": "проти правосуддя",
    "life": "проти життя та здоров",
    "public_safety": "проти громадської безпеки",
    "state_authority": "проти авторитету органів державної влади",
}
# "Усього КП > які виявлено працівниками > органів …" in table 1.1: offences detected by each agency (the per-agency
# tables 2–6 are split into section subtables with no overall row, so they are not used)
AGENCIES = {"органів Національної поліції": "police", "органів безпеки": "sbu", "органів Бюро економічної безпеки": "beb",
            "органів Державного бюро розслідувань": "dbr", "органів Національного антикорупційного бюро": "nabu",
            "органів прокуратури": "prosecutors"}
MONEY = {"Сума неправомірної вигоди": "bribe_uah_k", "Встановлена сума матеріальних збитків": "damage_uah_k",
         "Відшкодовано збитків": "compensated_uah_k"}


def labels(dm):
    return [dm[f"level{i}Name"].strip() for i in range(1, 6) if dm.get(f"level{i}Name")]


def key_forma_1(c):
    """(key, measure) for the forma_1 cells we keep, else None. m1 = registered in the period, m2 = suspicion served
    (the metric names vary between layouts — 'Обліковано … звітному періоді', 'Графа 1' — the codes do not)."""
    t, m, dm = c["tableCode"], c["metricCode"], c["dimensions"]
    L, sub = labels(dm), (dm.get("subtableName") or "").strip()
    if not L:
        return None
    if t == "8" and m == "m1" and (sub == "" or sub.startswith("Таблиця 8.1")):  # money: first column, all offences
        for k, v in MONEY.items():
            if L[0].startswith(k) and len(L) == 1:
                return (f"money:{v}", "value")
        return None
    meas = {"m1": "registered", "m2": "suspicion"}.get(m)
    if not meas:
        return None
    if t != "1":
        return None
    a = ART.search(L[-1])
    if a and (len(L) == 1 or L[-2] in ("за видами правопорушень", "за видами", "за пидами правопорушень")):
        return (f"art:{a.group(1)}", meas) if meas == "registered" or a.group(1) in CURATED else None
    if L == ["Усього кримінальних правопорушень"]:
        if sub in ("", "Таблиця 1.1."):
            return ("total", meas)
        for k, s in SECTIONS.items():
            if s in sub:
                return (f"sec:{k}", meas)
        return None
    if len(L) == 1:
        for k, s in SECTIONS.items():
            if s in L[0]:
                return (f"sec:{k}", meas)
    if len(L) == 3 and L[:2] == ["Усього кримінальних правопорушень", "які виявлено працівниками"] and L[2] in AGENCIES and \
            sub in ("", "Таблиця 1.1."):
        return (f"detected_by:{AGENCIES[L[2]]}", meas)
    return None


def key_forma_2(c):
    """Persons: m1 = persons served a suspicion, m5 = persons identified as offenders; rows by article in the
    'злочини' and 'кримінальні проступки' tables (1, 1.1, 4) and the totals."""
    t, m, dm = c["tableCode"], c["metricCode"], c["dimensions"]
    if t not in ("1", "1.1"):
        return None
    meas = {"m1": "persons_suspected", "m5": "persons_identified"}.get(m)
    L = labels(dm)
    if not meas or not L:
        return None
    a = ART.search(L[-1])
    if a and len(L) <= 2 and a.group(1) in CURATED:
        return (f"art:{a.group(1)}", meas)
    return None


def key_forma_1_oz(c):
    """Organised groups: first column of every row in table 1 (detected groups, cases, persons)."""
    L = labels(c["dimensions"])
    if c["tableCode"] != "1" or c["metricCode"] != "m1" or not L or len(L) > 2:
        return None
    return ("oz:" + " / ".join(L)[:120], "value")


LFT_ROWS = {"Обліковано у звітному періоді кримінальних правопорушень": "registered",
            "Кількість кримінальних правопорушень, досудове розслідування у яких проводилося у звітному періоді": "investigated",
            "у т.ч.: / Кількість осіб, які їх учинили, встановлені": "persons_identified"}


def key_forma_1_lft(c):
    """Legalisation (1-ЛФТ): the columns are articles (ст.209, ст.209-1), the rows are measures; table 1 only."""
    L = labels(c["dimensions"])
    if c["tableCode"] == "1.1":  # 2023+ layout: columns are bare article numbers (209, 209-1, 258-5, 306, 332, …)
        a = re.match(r"(\d{3}(?:-\d)?)$", c["metricName"].strip())
        meas = {"Розслідувалося кримінальних проваджень": "investigated",
                "Розслідувалося кримінальних проваджень / розпочато проваджень у звітному періоді": "started"}.get(" / ".join(L))
        return (f"art:{a.group(1)}", meas) if a and meas else None
    a = re.match(r"ст\.\s*(209(?:-1)?)$", c["metricName"].strip())
    meas = LFT_ROWS.get(" / ".join(L))
    return (f"art:{a.group(1)}", meas) if c["tableCode"] == "1" and a and meas else None


def section_row(c):
    """forma_1 table 1 rows that are a whole Criminal Code section (no subtable, one label): summed per report into
    'sections_published'. Since 2020 each section has a 'Кримінальні правопорушення …' row (crimes + misdemeanours) and
    sometimes a 'Злочини …' row (crimes only); per section the former is taken when present, else the latter."""
    dm, L = c["dimensions"], labels(c["dimensions"])
    if c["tableCode"] != "1" or len(L) != 1 or (dm.get("subtableName") or "").strip():
        return None
    meas = {"m1": "registered", "m2": "suspicion"}.get(c["metricCode"])
    for pre, kind in (("Кримінальні правопорушення ", "kp"), ("Злочини ", "zl")):
        if meas and L[0].startswith(pre) and re.match(r"(проти|у сфері)\b", L[0][len(pre):]):
            return (kind, L[0][len(pre):].strip().lower()[:60], meas)  # section name without the prefix
    return None


KEYS = {"forma_1": key_forma_1, "forma_2": key_forma_2, "forma_1_oz": key_forma_1_oz, "forma_1_lft": key_forma_1_lft}


def download(form, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{form}.json"
    # curl: data.gov.ua answers 403 to urllib's default user agent
    subprocess.run(["curl", "-sSfL", "--retry", "3", "-o", str(p), FORMS[form]], check=True)
    return p


def parse(form, path: Path):
    """-> rows (period_from, period_to, key, measure, value, label). Streams cells first (to map id -> key), then reports;
    when a layout has several cells for one key (duplicated subtables), they must agree, else the max is kept."""
    import ijson
    keyf = KEYS[form]
    want, label, secs = {}, {}, {}
    with open(path, "rb") as f:
        for c in ijson.items(f, "cells.item"):
            k = keyf(c)
            if k:
                want[str(c["id"])] = k
                label.setdefault(k[0], (labels(c["dimensions"]) or [c["metricName"]])[-1][:200])
            if form == "forma_1" and section_row(c):
                secs[str(c["id"])] = section_row(c)
    out, clash = {}, 0
    with open(path, "rb") as f:
        for r in ijson.items(f, "reports.item"):
            p = r["period"]
            pf, pt = date(p["year"], p["monthFrom"], 1), date(p["year"], p["monthTo"], 1)
            ss = defaultdict(dict)  # (kind, measure) -> {section label: value}; duplicate layouts of one row collapse
            for cid, v in (r.get("values") or {}).items():
                if cid in secs and v is not None:
                    kind, lab, meas = secs[cid]
                    ss[(kind, meas)][lab] = max(float(v), ss[(kind, meas)].get(lab, 0.0))
            for meas in ("registered", "suspicion"):
                part = {**ss.get(("zl", meas), {}), **ss.get(("kp", meas), {})}
                if part:
                    out[(pf, pt, "sections_published", meas)] = sum(part.values())
                    label["sections_published"] = f"сума {len(part)} опублікованих розділів КК"
            for cid, v in (r.get("values") or {}).items():
                k = want.get(cid)
                if k is None or v is None:
                    continue
                v = float(v)
                kk = (pf, pt) + k
                if kk in out and out[kk] != v:
                    clash += 1
                    v = max(v, out[kk])
                out[kk] = v
    rows = [(pf, pt, key, meas, v, label.get(key)) for (pf, pt, key, meas), v in out.items()]
    return rows, clash


def write(cur, form, rows):
    """Replace the form's rows (each file carries the whole history, and published figures get revised). A parse that
    lost more than half of the stored rows (a new layout the label rules do not know) is refused instead."""
    cur.execute("SELECT count(*) FROM ogp_stats WHERE form = %s", (form,))
    have = cur.fetchone()[0]
    if len(rows) < have / 2:
        raise SystemExit(f"{form}: parsed {len(rows)} rows, {have} stored: layout changed? not replacing")
    cur.execute("DELETE FROM ogp_stats WHERE form = %s", (form,))
    cur.execute("DELETE FROM ogp_keys WHERE form = %s", (form,))
    with cur.copy("COPY ogp_stats (form, period_from, period_to, key, measure, value) FROM STDIN") as cp:
        for pf, pt, key, meas, v, _ in rows:
            cp.write_row((form, pf, pt, key, meas, v))
    labs = {key: lab for _, _, key, _, _, lab in rows}
    with cur.copy("COPY ogp_keys (form, key, label) FROM STDIN") as cp:
        for key, lab in labs.items():
            cp.write_row((form, key, lab))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default=",".join(FORMS))
    ap.add_argument("--dir", default=str(ROOT / "data" / "ogp"))
    ap.add_argument("--no-download", action="store_true", help="parse files already in --dir")
    ap.add_argument("--dry-run", action="store_true", help="parse and print a summary, no database")
    a = ap.parse_args()
    conn = None
    if not a.dry_run:
        from load_to_db import connect
        conn = connect()
    for form in a.forms.split(","):
        p = Path(a.dir) / f"{form}.json" if a.no_download else download(form, Path(a.dir))
        rows, clash = parse(form, p)
        keys = defaultdict(int)
        for r in rows:
            keys[r[2].split(":")[0]] += 1
        periods = sorted({r[1] for r in rows})
        print(f"{form}: {len(rows)} rows, {len({r[2] for r in rows})} keys {dict(keys)}, periods {periods[0] if periods else '-'}..{periods[-1] if periods else '-'}"
              f" ({len(periods)}), conflicting duplicate cells {clash}")
        if conn:
            with conn.cursor() as cur:
                write(cur, form, rows)
            conn.commit()


if __name__ == "__main__":
    main()
