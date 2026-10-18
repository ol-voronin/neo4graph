"""Match organisations named in entity extractions against the full ЄДР dump (Ministry of Justice, data.gov.ua,
weekly). Gives the strong key the graph needs: EDRPOU, plus founders, beneficiaries and signers as the registry
lists them for a legal entity. Only companies that appear in case material are kept; the dump itself is never loaded.

Runs in GitHub Actions (the dump is 330 MB zipped, several GB of XML): streams the XML, keeps records whose
normalised name matches one of the target names, upserts them into edr_orgs (migration 008).

Usage:
  python src/edr_match.py --download                # fetch uo.zip into data/edr/ (Actions)
  python src/edr_match.py --names "ТОВ Газ-мдс,..." # match explicit names, print JSON
  python src/edr_match.py --from-db                 # names from ee_orgs (kind=company), upsert into edr_orgs
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DUMP_URL = "https://data.gov.ua/dataset/03cc1239-3988-4451-aa0d-aadb77448714/resource/d40cc921-39bb-44fd-be06-dc02589f45c6/download/uo.zip"
DUMP = ROOT / "data" / "edr" / "uo.zip"
OPF = r"\b(тов|тзов|пат|прат|ат|тдв|дп|кп|пп|фг|го|бф|ооо|зао|оао|ао|llc|ltd|inc)\b"
OPF_FULL = r"(товариство з обмеженою відповідальністю|товариство з додатковою відповідальністю|приватне акціонерне товариство|" \
           r"публічне акціонерне товариство|акціонерне товариство|державне підприємство|комунальне підприємство|" \
           r"приватне підприємство|фермерське господарство|громадська організація|благодійний фонд|благодійна організація)"


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е").replace("’", "'").replace("`", "'")
    s = re.sub(r"[«»\"“”„']", " ", s)
    s = re.sub(OPF_FULL, " ", s)
    s = re.sub(OPF, " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def download():
    DUMP.parent.mkdir(parents=True, exist_ok=True)
    print("downloading", DUMP_URL)
    req = urllib.request.Request(DUMP_URL, headers={"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)"})  # bare urllib UA gets 403
    with urllib.request.urlopen(req, timeout=120) as r, open(DUMP, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    print("done", DUMP.stat().st_size)


def texts(el, tag):
    return [x.text.strip() for x in el.iter(tag) if x.text and x.text.strip()]


def stream(targets: dict):
    """targets: norm_name -> original query. Yields matched records."""
    with zipfile.ZipFile(DUMP) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".xml")]
        for n in names:
            with z.open(n) as raw:
                for _, el in ET.iterparse(io.BufferedReader(raw, 1 << 20), events=("end",)):
                    if el.tag != "SUBJECT":
                        continue
                    name = (el.findtext("NAME") or "")
                    short = (el.findtext("SHORT_NAME") or "")
                    hit = targets.get(norm(name)) or targets.get(norm(short))
                    if hit:
                        yield {
                            "query": hit, "edrpou": el.findtext("EDRPOU"), "name": name, "short_name": short,
                            "stan": el.findtext("STAN"), "opf": el.findtext("OPF"),
                            "founders": texts(el, "FOUNDER"), "beneficiaries": texts(el, "BENEFICIARY"),
                            "signers": texts(el, "SIGNER"), "authorized_capital": el.findtext("AUTHORIZED_CAPITAL"),
                        }
                    el.clear()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--names", default="")
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.download:
        download()
    queries = [x.strip() for x in a.names.split(",") if x.strip()]
    conn = cur = None
    if a.from_db:
        sys.path.insert(0, str(ROOT / "src"))
        from load_to_db import connect
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT name FROM ee_orgs WHERE kind IN ('company','other') AND name IS NOT NULL")
        queries += [r[0] for r in cur.fetchall()]
    targets = {norm(q): q for q in queries if len(norm(q)) >= 4}
    print(f"targets: {len(targets)}")
    if not targets:
        return
    if not DUMP.exists():
        sys.exit(f"dump missing: {DUMP} (run with --download)")
    n = 0
    for rec in stream(targets):
        n += 1
        print(json.dumps(rec, ensure_ascii=False)[:400])
        if cur and not a.dry_run:
            cur.execute(
                "INSERT INTO edr_orgs(edrpou, name, short_name, stan, opf, founders, beneficiaries, signers, authorized_capital, matched_queries) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,ARRAY[%s]) ON CONFLICT (edrpou) DO UPDATE SET name=EXCLUDED.name, "
                "short_name=EXCLUDED.short_name, stan=EXCLUDED.stan, founders=EXCLUDED.founders, beneficiaries=EXCLUDED.beneficiaries, "
                "signers=EXCLUDED.signers, authorized_capital=EXCLUDED.authorized_capital, "
                "matched_queries=(SELECT array_agg(DISTINCT q) FROM unnest(edr_orgs.matched_queries || EXCLUDED.matched_queries) q), updated_at=now()",
                (rec["edrpou"], rec["name"], rec["short_name"], rec["stan"], rec["opf"], json.dumps(rec["founders"], ensure_ascii=False),
                 json.dumps(rec["beneficiaries"], ensure_ascii=False), json.dumps(rec["signers"], ensure_ascii=False),
                 rec["authorized_capital"], rec["query"]))
            conn.commit()
    print(f"matched {n} registry records for {len(targets)} names")


if __name__ == "__main__":
    main()
