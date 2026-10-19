"""Media mentions for a case: who the press names in connection with a case number, in which role, with the quote and
URL. Stored in media_links as media claims only. This step does NOT match names to the anonymised ОСОБА_N roles of
the court register: that anonymisation is deliberate (Law on Access to Court Decisions) and is not reversed here.

  python src/link_media_names.py --cause 932/2641/25 [--provider anthropic|gemini]
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import anthropic_text, gemini_call, parse_json  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)"}
SYSTEM = """Ти збираєш, кого ЗМІ називають у звʼязку з конкретною судовою справою (за її номером або описом), і в якій ролі.
Поверни ТІЛЬКИ JSON: {"mentions":[{"name":"як у джерелі","name_norm":"Прізвище Імʼя","role":"suspect|defendant|lawyer|prosecutor|judge|official|witness|victim|other",
"position":"посада/статус за джерелом","quote":"<=200 символів цитати, де названо імʼя і роль","url":"джерело","confidence":0.0}]}
Правила: лише те, що джерело прямо пише; публічні посадовці, адвокати, прокурори, судді, керівники компаній, або особи, яких джерело прямо називає підозрюваними/обвинуваченими; приватних осіб без ролі не додавай; нічого не вигадуй."""


def page_text(url, limit=6000):
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
    except Exception:  # noqa: BLE001
        return None
    html = raw.decode("utf-8", "replace")
    txt = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\\1>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\\s+", " ", txt).strip()[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cause", required=True)
    ap.add_argument("--provider", default="gemini")
    ap.add_argument("--max-pages", type=int, default=6)
    a = ap.parse_args()
    key = "ANTHROPIC_API_KEY" if a.provider == "anthropic" else "GEMINI_API_KEY"
    if not os.environ.get(key):
        sys.exit(f"{key} is not set")
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS media_links (id bigserial PRIMARY KEY, cause_num text, name_norm text, name text, role text, "
                "position text, quote text, url text, confidence real, model text, created_at timestamptz DEFAULT now(), UNIQUE (cause_num, name_norm, url))")
    cur.execute("SELECT url, title, snippet FROM web_mentions WHERE query ILIKE %s ORDER BY fetched_at DESC LIMIT 20", ("%" + a.cause + "%",))
    web = cur.fetchall()
    cur.execute("SELECT id, url, left(text, 3000) FROM messages WHERE source_id <> 'edrsr:court' AND text LIKE %s LIMIT 10", ("%" + a.cause + "%",))
    posts = cur.fetchall()
    print(f"case {a.cause}: {len(web)} web hits, {len(posts)} corpus posts")
    if not web and not posts:
        print("no media sources yet")
        return
    sources = [{"url": u, "text": (t or "") + " " + (page_text(u) or sn or "")} for u, t, sn in web[: a.max_pages]]
    sources += [{"url": u, "text": t} for _, u, t in posts]
    user = json.dumps({"cause_num": a.cause, "sources": sources}, ensure_ascii=False)[:60000]
    if a.provider == "anthropic":
        import anthropic
        txt = anthropic_text(anthropic.Anthropic().messages.create(model="claude-haiku-5-5", max_tokens=4000, output_config={"effort": "low"},
                                                                 system=SYSTEM, messages=[{"role": "user", "content": user}]))
    else:
        txt = gemini_call("gemini-3.5-flash-lite", SYSTEM, user, os.environ["GEMINI_API_KEY"], max_tokens=4000)
    items = (parse_json(txt) or {}).get("mentions") or []
    n = 0
    for l in items:
        if not l.get("name_norm") or not l.get("url"):
            continue
        cur.execute("INSERT INTO media_links(cause_num, name_norm, name, role, position, quote, url, confidence, model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (cause_num, name_norm, url) DO UPDATE SET role=EXCLUDED.role, quote=EXCLUDED.quote, confidence=EXCLUDED.confidence",
                    (a.cause, l["name_norm"][:120], (l.get("name") or "")[:160], l.get("role") or "other", (l.get("position") or "")[:200],
                     (l.get("quote") or "")[:300], l["url"][:500], float(l.get("confidence") or 0.5), "claude-haiku-5-5" if a.provider == "anthropic" else "gemini"))
        n += 1
    conn.commit()
    print(f"media mentions stored: {n}")


if __name__ == "__main__":
    main()
