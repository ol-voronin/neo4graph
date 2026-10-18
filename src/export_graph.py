"""Export graph data for the explorer page: graph.json (entities, registry links, evidence posts) and
decisions.json (court decisions index by case number). Runs where DATABASE_URL works (GitHub Actions).

  python src/export_graph.py --out data/graph
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/graph")
    a = ap.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT cause_num, doc_id, court, judgment_form, justice_kind, adjudication_date, judge, doc_url, status, "
                "(text IS NOT NULL) AS has_text FROM court_decisions WHERE cause_num IS NOT NULL AND cause_num <> '' ORDER BY cause_num, adjudication_date")
    by_case = {}
    for cause, doc_id, court, form, kind, d, judge, url, status, has_text in cur.fetchall():
        by_case.setdefault(cause, []).append({"id": doc_id, "c": court, "f": form, "k": kind, "d": str(d) if d else None,
                                              "j": judge, "u": url, "s": status, "t": has_text})
    (out / "decisions.json").write_text(json.dumps({"cases": by_case}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"decisions.json: {len(by_case)} cases, {sum(len(v) for v in by_case.values())} decisions")
    cur.execute("SELECT id, cause_num, left(regexp_replace(text, '\\s+', ' ', 'g'), 260), url, ts_published::date FROM messages WHERE source_id='edrsr:court'")
    print("decision texts as messages:", len(cur.fetchall()))


if __name__ == "__main__":
    main()
