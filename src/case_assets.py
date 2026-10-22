"""Property named in court decisions -> case_assets (migration 025). Reads decision texts that talk about arresting,
seizing or returning property, sends only the paragraphs about property to Claude (Haiku), stores one row per item.
Also fills court_decisions.erdr (ЄРДР numbers) for texts that arrived since the last run.

The register depersonalises private persons («ОСОБА_5», «НОМЕР_3» for VINs and plates), so items are tied to the case and
the proceeding, not to a name; the graph links a person to them only through their cases (see web/api/_assets.js).

  python src/case_assets.py [--limit 200] [--dry-run]
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import anthropic_text, parse_json, claude  # noqa: E402

MODEL = "claude-haiku-5-5"
KINDS = {"vehicle", "real_estate", "land", "money", "account", "shares", "company", "weapon", "other"}
ACTIONS = {"arrested", "arrest_lifted", "confiscated", "seized", "returned", "named"}
# texts worth reading: an arrest / seizure / confiscation / return of property
CANDIDATE_RE = r"(арешт\w*|вилуч\w*|конфіскац\w*|повернут\w*)[^.]{0,200}(майн|автомоб|транспорт|квартир|будин|нерухом|земельн|рахун|кошт|акці|частк|корпоратив)"
PARA_RE = re.compile(r"(арешт|вилуч|конфіск|майн|автомоб|транспорт|квартир|будин|нерухом|земельн|рахун|кошт|акці|частк|VIN|ДНЗ|кадастр|реєстраційний номер|ЄДРПОУ|площ)", re.I)
SYSTEM = """Ти витягуєш із судового рішення перелік майна, щодо якого суд ухвалив рішення (арешт, скасування арешту, конфіскація, вилучення, повернення) або яке рішення прямо називає як предмет провадження. Поверни ТІЛЬКИ JSON:
{"assets":[{"kind":"vehicle|real_estate|land|money|account|shares|company|weapon|other","label":"коротко: що це («AUDI Q8 2019», «1/4 квартири 99,3 м²», «кошти на рахунках у ПриватБанку»)","action":"arrested|arrest_lifted|confiscated|seized|returned|named","holder":"чиє майно як написано в тексті («ОСОБА_5», назва компанії, «підозрюваний») або null","brand":null,"model":null,"year":null,"area":null,"share":"частка, якщо вказано","reg_number":"реєстраційний номер об'єкта нерухомого майна, якщо не знеособлений","cadastral":"кадастровий номер, якщо є","bank":"банк для рахунків/коштів","edrpou":"код ЄДРПОУ компанії чи банку, якщо названо","amount":null,"currency":null}]}
Знеособлені значення («НОМЕР_3», «АДРЕСА_1», «ОСОБА_2») не вигадуй і не розкривай: став null (holder можна лишити як «ОСОБА_2»). Одне майно — один запис, навіть якщо згадується кілька разів. Нічого не додавай зі своїх знань. Майна немає — {"assets":[]}."""


def fragments(text, limit=9000):
    """paragraphs about property only (decisions run to 30 k chars; the property list is a small part)"""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=\d+\.|-|–)", text or "") if p.strip()]
    keep = [p for p in paras if PARA_RE.search(p)]
    out, n = [], 0
    for p in keep:
        if n + len(p) > limit:
            break
        out.append(p)
        n += len(p)
    return "\n\n".join(out) or (text or "")[:limit]


def _int(v):
    try:
        y = int(str(v)[:4])
        return y if 1900 < y < 2100 else None
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def clean(o):
    out = []
    for a in (o.get("assets") or []):
        if not isinstance(a, dict) or not a.get("label"):
            continue
        out.append(dict(kind=a.get("kind") if a.get("kind") in KINDS else "other", label=str(a["label"])[:200],
                        action=a.get("action") if a.get("action") in ACTIONS else "named", holder=(a.get("holder") or None),
                        brand=a.get("brand") or None, model=a.get("model") or None, year=_int(a.get("year")), area=_num(a.get("area")),
                        share=a.get("share") or None, reg_number=a.get("reg_number") if re.fullmatch(r"\d{6,16}", str(a.get("reg_number") or "")) else None,
                        cadastral=a.get("cadastral") if re.fullmatch(r"\d{10}:\d{2}:\d{3}:\d{4}", str(a.get("cadastral") or "")) else None,
                        bank=a.get("bank") or None, edrpou=a.get("edrpou") if re.fullmatch(r"\d{8}", str(a.get("edrpou") or "")) else None,
                        amount=_num(a.get("amount")), currency=a.get("currency") or None))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    # ЄРДР numbers for texts that arrived since the last run
    cur.execute("UPDATE court_decisions d SET erdr = x.e FROM (SELECT doc_id, array_agg(DISTINCT m[1]) e FROM court_decisions, "
                "regexp_matches(text, '\\m(\\d{17})\\M', 'g') m WHERE text IS NOT NULL AND erdr IS NULL GROUP BY 1) x WHERE d.doc_id = x.doc_id")
    print(f"erdr filled: {cur.rowcount}")
    conn.commit()
    cur.execute("SELECT d.doc_id, d.cause_num, d.erdr, d.adjudication_date, d.text FROM court_decisions d LEFT JOIN asset_extractions x ON x.doc_id = d.doc_id "
                "WHERE x.doc_id IS NULL AND d.text ~* %s ORDER BY d.adjudication_date DESC NULLS LAST LIMIT %s", (CANDIDATE_RE, a.limit))
    rows = cur.fetchall()
    print(f"candidates: {len(rows)}")
    client = claude()
    ok = items = 0
    for doc_id, cause, erdr, d, text in rows:
        try:
            r = client.messages.create(model=MODEL, max_tokens=4000, output_config={"effort": "low"}, system=SYSTEM,
                                       messages=[{"role": "user", "content": f"Справа {cause}, рішення від {d}.\n\n{fragments(text)}"}])
            assets = clean(parse_json(anthropic_text(r)))
        except Exception as e:  # noqa: BLE001
            print(f"skip {doc_id}: {e}")
            continue
        ok += 1
        items += len(assets)
        if a.dry_run:
            print(doc_id, cause, "|", "; ".join(f"{x['kind']}:{x['label']} ({x['action']}, {x['holder']})" for x in assets)[:400])
            continue
        for i, x in enumerate(assets, 1):
            cur.execute("INSERT INTO case_assets(doc_id, idx, cause_num, erdr, decision_date, kind, label, action, holder, brand, model, year, area, share, reg_number, "
                        "cadastral, bank, edrpou, amount, currency, details) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (doc_id, idx) DO NOTHING",
                        (doc_id, i, cause, erdr, d, x["kind"], x["label"], x["action"], x["holder"], x["brand"], x["model"], x["year"], x["area"], x["share"],
                         x["reg_number"], x["cadastral"], x["bank"], x["edrpou"], x["amount"], x["currency"], json.dumps({}, ensure_ascii=False)))
        cur.execute("INSERT INTO asset_extractions(doc_id, n, model) VALUES (%s,%s,%s) ON CONFLICT (doc_id) DO NOTHING", (doc_id, len(assets), MODEL))
        conn.commit()
    print(f"read {ok} decisions, {items} items")


if __name__ == "__main__":
    main()
