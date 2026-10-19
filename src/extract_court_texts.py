"""Court decision texts -> messages (source 'edrsr:court') -> entity extraction, so decisions join the graph like posts.

  python src/extract_court_texts.py [--cause 642/8729/21] [--limit 40] [--provider anthropic|gemini]
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cause", default="")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--provider", default="gemini")
    a = ap.parse_args()
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO sources(id,name,country,lang,type,platform,tier) VALUES ('edrsr:court','Єдиний державний реєстр судових рішень','UA','uk','official','registry','A') ON CONFLICT (id) DO NOTHING")
    cur.execute("SELECT doc_id, cause_num, court, judgment_form, adjudication_date, doc_url, text FROM court_decisions "
                "WHERE text IS NOT NULL AND length(text) > 300 " + ("AND cause_num = %s " if a.cause else "") + "ORDER BY adjudication_date DESC NULLS LAST LIMIT %s",
                ((a.cause, a.limit) if a.cause else (a.limit,)))
    rows = cur.fetchall()
    n = 0
    for doc_id, cause, court, form, d, url, text in rows:
        mid = f"edrsr:{doc_id}"
        head = f"Справа {cause} · {court} · {form} · {d}\n\n"
        cur.execute("INSERT INTO messages(id, source_id, ts_published, lang, text, url, hash) VALUES (%s,'edrsr:court',%s,'uk',%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    (mid, (str(d) + "T12:00:00+03:00") if d else "2026-01-01T00:00:00+03:00", (head + text)[:12000], url, doc_id))
        n += cur.rowcount
    conn.commit()
    print(f"decision texts as messages: +{n} (of {len(rows)})")
    if rows:
        r = subprocess.run([sys.executable, "src/extract_entities.py", "--sources", "edrsr:court", "--limit", str(a.limit),
                            "--min-len", "300"] + (["--provider", a.provider] if a.provider else []), cwd=ROOT, capture_output=True, text=True)
        print((r.stdout + r.stderr)[-2000:])


if __name__ == "__main__":
    main()
