"""Ingest a public web page as a source: fetch, strip to text, store as a message (source 'web:<domain>'), then run entity
extraction on it so the people, roles and cases it names join the graph with the page as evidence.
  python src/ingest_url.py "https://example.com/article" [--cause 932/2641/25]
"""
import argparse
import hashlib
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
UA = {"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)"}


def fetch(url):
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read()
    if raw[:5] == b"{\\rtf":
        from edrsr_index import rtf_to_text
        return "RTF", rtf_to_text(raw)
    html = raw.decode("utf-8", "replace")
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    body = re.sub(r"<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    body = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", body)).strip()
    return (re.sub(r"\s+", " ", title.group(1)).strip() if title else url), body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--cause", default="")
    ap.add_argument("--provider", default="anthropic")
    a = ap.parse_args()
    url = a.url.strip()
    dom = urllib.parse.urlparse(url).netloc
    title, text = fetch(url)
    text = text.replace("\x00", "")
    if len(text) < 200:
        sys.exit(f"page text too short ({len(text)} chars): {url}")
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    sid = "web:" + dom
    cur.execute("INSERT INTO sources(id,name,country,lang,type,platform,tier) VALUES (%s,%s,'UA','uk','independent','web','B') ON CONFLICT (id) DO NOTHING", (sid, dom))
    mid = "web:" + hashlib.sha256(url.encode()).hexdigest()[:20]
    head = (f"Справа {a.cause} · " if a.cause else "") + title + "\n\n"
    cur.execute("INSERT INTO messages(id, source_id, ts_published, lang, text, url, hash) VALUES (%s,%s,%s,'uk',%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (mid, sid, datetime.now(timezone.utc).isoformat(), (head + text)[:12000], url, hashlib.sha256(text.encode()).hexdigest()[:32]))
    conn.commit()
    print(f"stored {mid} from {dom}: {title[:80]} ({len(text)} chars)")
    import subprocess
    r = subprocess.run([sys.executable, "src/extract_entities.py", "--sources", sid, "--limit", "5", "--min-len", "200", "--provider", a.provider], cwd=ROOT, capture_output=True, text=True)
    print((r.stdout + r.stderr)[-1500:])
    if a.cause:
        cur.execute("INSERT INTO query_hits(query, message_id, where_found) VALUES (%s,%s,'url') ON CONFLICT DO NOTHING", (a.cause, mid))
        conn.commit()


if __name__ == "__main__":
    main()
