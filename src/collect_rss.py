"""Keyless RSS/Atom collection for sources without a Telegram channel (Western/international media, US/UK officials).
Same record format as collect_telegram.py, written to data/rss/<name>.jsonl; load_to_db.py messages picks it up and
registers the source from SOURCES. Broad world feeds keep only items on the war (TOPIC_RE); Ukraine/Russia feeds keep all.
Paywalled outlets (NYT, FT, WSJ, Economist) give headline + summary only.

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
    "bbc": ["https://feeds.bbci.co.uk/news/world/europe/rss.xml", "https://feeds.bbci.co.uk/news/world/rss.xml"],
    "nytimes": ["https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"],
    "guardian": ["https://www.theguardian.com/world/ukraine/rss", "https://www.theguardian.com/world/russia/rss"],
    "ft": ["https://www.ft.com/world/europe?format=rss"],
    "wsj": ["https://feeds.a.dj.com/rss/RSSWorldNews.xml"],
    "economist": ["https://www.economist.com/europe/rss.xml"],
    "politico_eu": ["https://www.politico.eu/feed/"],
    "dw": ["https://rss.dw.com/rdf/rss-en-world"],
    "euronews": ["https://www.euronews.com/rss?level=theme&name=news"],
    "aljazeera": ["https://www.aljazeera.com/xml/rss/all.xml"],
    "state_dept": ["https://www.state.gov/rss-feed/press-releases/feed/"],
    "uk_mod": ["https://www.gov.uk/government/organisations/ministry-of-defence.atom"],
    "un_news": ["https://news.un.org/feed/subscribe/en/news/region/europe/feed/rss.xml"],
}
# source_id suffix -> (name, country, type); tier B like the other media
SOURCES = {
    "whitehouse": ("The White House", "US", "official"), "bbc": ("BBC News", "GB", "independent"),
    "nytimes": ("The New York Times", "US", "independent"), "guardian": ("The Guardian", "GB", "independent"),
    "ft": ("Financial Times", "GB", "independent"), "wsj": ("The Wall Street Journal", "US", "independent"),
    "economist": ("The Economist", "GB", "independent"), "politico_eu": ("Politico Europe", "BE", "independent"),
    "dw": ("Deutsche Welle", "DE", "state_media"), "euronews": ("Euronews", "FR", "independent"),
    "aljazeera": ("Al Jazeera", "QA", "state_media"), "state_dept": ("U.S. Department of State", "US", "official"),
    "uk_mod": ("UK Ministry of Defence", "GB", "official"), "un_news": ("UN News", "UN", "official"),
}
ALL_ITEMS = {"whitehouse", "guardian"}  # topical already: keep everything
TOPIC_RE = re.compile(r"ukrain|kyiv|kiev|russia|kremlin|putin|zelensk|moscow|nato|crimea|donbas|kharkiv|odesa|odessa|"
                      r"dnipro|zaporizh|kherson|missile|drone|ballistic|ceasefire|peace talks|sanction|nuclear|belarus|"
                      r"україн|росі|путін|зеленськ|україн|россия|украин|путин", re.I)
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


NS = {"rss1": "http://purl.org/rss/1.0/", "atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/",
      "content": "http://purl.org/rss/1.0/modules/content/"}


def when(it):
    """RSS pubDate (RFC 822), or ISO dates of RDF dc:date / Atom updated/published."""
    pub = it.findtext("pubDate")
    try:
        if pub:
            return parsedate_to_datetime(pub).isoformat()
    except (TypeError, ValueError):
        pass
    iso = it.findtext("dc:date", namespaces=NS) or it.findtext("atom:published", namespaces=NS) or it.findtext("atom:updated", namespaces=NS)
    return iso.strip() if iso else None


def fetch(url: str):
    """RSS 2.0 <item>, RSS 1.0/RDF {rss1}item (DW) and Atom <entry> (gov.uk)."""
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
    root = ET.fromstring(raw)
    for it in [*root.iter("item"), *root.iter(f"{{{NS['rss1']}}}item")]:
        link = (it.findtext("link") or it.findtext("rss1:link", namespaces=NS) or "").strip()
        title = clean(it.findtext("title") or it.findtext("rss1:title", namespaces=NS) or "")
        body = (it.findtext("content:encoded", namespaces=NS) or it.findtext("description")
                or it.findtext("rss1:description", namespaces=NS) or "")
        yield link, title, clean(body), when(it)
    for it in root.iter(f"{{{NS['atom']}}}entry"):
        ln = it.find("atom:link", NS)
        link = (ln.get("href") if ln is not None else "") or ""
        body = it.findtext("atom:summary", namespaces=NS) or it.findtext("atom:content", namespaces=NS) or ""
        yield link.strip(), clean(it.findtext("atom:title", namespaces=NS) or ""), clean(body), when(it)


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
            if name not in ALL_ITEMS and not TOPIC_RE.search(text):
                continue
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
