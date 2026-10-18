"""Keyless RSS collection for sources without a Telegram channel (US official feeds). Same record format as
collect_telegram.py, written to data/rss/<name>.jsonl; load_to_db.py messages picks it up.

Usage: python src/collect_rss.py --all   |   python src/collect_rss.py --feed whitehouse
"""
import argparse
import hashlib
import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEEDS = {
    # source_id suffix -> list of feed urls (merged, de-duplicated by link)
    "whitehouse": ["https://www.whitehouse.gov/news/feed/", "https://www.whitehouse.gov/briefings-statements/feed/"],
}
UA = {"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)"}
TAG_RE = re.compile(r"<[^>]+>")


NAV_RE = re.compile(r"^(Search|Select Category|All( News)?|Briefings & Statements|(All )?Presidential Actions|Executive Orders|"
                    r"Nominations & Appointments|Presidential Memoranda|Proclamations|Fact Sheets|Releases|Remarks|Research|Articles|"
                    r"Videos|Share|Facebook|X|Instagram|Email|Print|Copy link)\s*$", re.I)


def clean(s: str) -> str:
    s = html.unescape(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I))
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", s, flags=re.I)
    s = TAG_RE.sub(" ", s)
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in s.split("\n")]
    return "\n".join(l for l in lines if l and not NAV_RE.match(l)).strip()


def fetch(url: str):
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        title = clean(it.findtext("title") or "")
        body = it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or it.findtext("description") or ""
        pub = it.findtext("pubDate")
        try:
            ts = parsedate_to_datetime(pub).isoformat() if pub else None
        except (TypeError, ValueError):
            ts = None
        yield link, title, clean(body), ts


def collect(name: str):
    out = ROOT / "data" / "rss" / f"{name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if out.exists():
        seen = {json.loads(l)["id"] for l in out.open(encoding="utf-8")}
    new = []
    for url in FEEDS[name]:
        for link, title, body, ts in fetch(url):
            mid = f"rss:{name}:{hashlib.sha256(link.encode()).hexdigest()[:16]}"
            if mid in seen or not link:
                continue
            seen.add(mid)
            text = (title + "\n\n" + body).strip()
            new.append({"id": mid, "source_id": f"rss:{name}", "msg_num": None, "ts_published": ts, "text": text[:12000],
                        "views": None, "url": link, "hash": hashlib.sha256(f"{name}|{text}".encode()).hexdigest()[:32]})
    with out.open("a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{name}: +{len(new)} new, total {len(seen)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    for n in (FEEDS if a.all else [a.feed]):
        try:
            collect(n)
        except Exception as e:  # noqa: BLE001
            print(f"{n}: failed: {e}")
