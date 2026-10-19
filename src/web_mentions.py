"""Web mentions of a query anywhere public: Google Programmable Search when GOOGLE_CSE_KEY + GOOGLE_CSE_ID are set
(100 free queries/day), otherwise DuckDuckGo's HTML endpoint (best effort, may be rate limited). Results go to
web_mentions and are shown in the graph as 'web' nodes.   python src/web_mentions.py "Кацуба Александр"
"""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
UA = {"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)"}


def google(q, key, cx, n=10):
    url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode({"key": key, "cx": cx, "q": q, "num": n, "hl": "uk"})
    data = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read())
    return [{"title": i.get("title"), "url": i.get("link"), "snippet": i.get("snippet"), "engine": "google"} for i in data.get("items", [])]


def brave(q, key, n=10):
    """Brave Search API (web): BRAVE_API_KEY. Free plan is enough for a few dozen objects a day."""
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": q, "count": n, "country": "ua", "search_lang": "uk", "safesearch": "off"})
    req = urllib.request.Request(url, headers={"Accept": "application/json", "X-Subscription-Token": key, "User-Agent": UA["User-Agent"]})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return [{"title": i.get("title"), "url": i.get("url"), "snippet": re.sub(r"<[^>]+>", "", i.get("description") or ""), "engine": "brave"}
            for i in (data.get("web") or {}).get("results", [])]


def tavily(q, key, n=10):
    """Tavily search API: TAVILY_API_KEY (1,000 credits/month free, no card)."""
    body = json.dumps({"api_key": key, "query": q, "max_results": n, "search_depth": "basic", "include_answer": False}).encode()
    req = urllib.request.Request("https://api.tavily.com/search", data=body, headers={"Content-Type": "application/json", "User-Agent": UA["User-Agent"]})
    data = json.loads(urllib.request.urlopen(req, timeout=40).read())
    return [{"title": i.get("title"), "url": i.get("url"), "snippet": (i.get("content") or "")[:400], "engine": "tavily"} for i in data.get("results", [])]


def ddg(q, n=10):
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q, "kl": "ua-uk"})
    page = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>.*?<a class="result__snippet"[^>]*>(.*?)</a>', page, re.S):
        href = html.unescape(m.group(1))
        if "uddg=" in href:
            href = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
        out.append({"title": html.unescape(re.sub(r"<[^>]+>", "", m.group(2))), "url": href,
                    "snippet": html.unescape(re.sub(r"<[^>]+>", "", m.group(3))), "engine": "ddg"})
        if len(out) >= n:
            break
    return out


def main():
    q = " ".join(sys.argv[1:]).strip()
    if len(q) < 3:
        sys.exit("query too short")
    key, cx, bk, tk = os.environ.get("GOOGLE_CSE_KEY"), os.environ.get("GOOGLE_CSE_ID"), os.environ.get("BRAVE_API_KEY"), os.environ.get("TAVILY_API_KEY")
    # Google "search the entire web" is gone for engines created after 2026-01-20, so Brave is the whole-web engine and a
    # Google engine (if any) is a curated-sites engine (up to 50 domains); both are used when both keys exist.
    hits, engines = [], []
    for name, fn in (("brave", lambda: brave(q, bk)), ("tavily", lambda: tavily(q, tk)), ("google", lambda: google(q, key, cx))):
        if (name == "brave" and not bk) or (name == "tavily" and not tk) or (name == "google" and not (key and cx)):
            continue
        try:
            hits += fn()
            engines.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"{name} failed:", str(e)[:200])
    if not engines:
        try:
            hits = ddg(q)
            engines = ["ddg (best effort)"]
        except Exception as e:  # noqa: BLE001
            print("search failed:", str(e)[:200])
    print(f"{q!r}: {len(hits)} hits via {'+'.join(engines) or 'nothing'}")
    for h in hits[:10]:
        print(" -", (h["title"] or "")[:70], "|", h["url"][:90])
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS web_mentions (query text, url text, title text, snippet text, engine text, domain text, "
                "fetched_at timestamptz DEFAULT now(), PRIMARY KEY (query, url))")
    for h in hits:
        dom = urllib.parse.urlparse(h["url"]).netloc
        cur.execute("INSERT INTO web_mentions(query, url, title, snippet, engine, domain) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (query, url) DO UPDATE SET "
                    "title=EXCLUDED.title, snippet=EXCLUDED.snippet, fetched_at=now()", (q, h["url"], h["title"], h["snippet"], h["engine"], dom))
    conn.commit()


if __name__ == "__main__":
    main()
