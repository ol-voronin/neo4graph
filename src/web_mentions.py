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


def tavily_keyless(q, n=10):
    """Tavily hosted MCP in keyless mode (no account; rate-limited). JSON-RPC tools/call over HTTP, SSE-framed reply."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "tavily_search", "arguments": {"query": q, "max_results": n}}}).encode()
    req = urllib.request.Request("https://mcp.tavily.com/mcp/", data=body, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                                                                                   "X-Tavily-Access-Mode": "keyless", "User-Agent": UA["User-Agent"]})
    raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    line = next((l[5:].strip() for l in raw.splitlines() if l.startswith("data:")), raw)
    msg = json.loads(line)
    if "error" in msg:
        raise RuntimeError(msg["error"].get("message", "mcp error"))
    inner = json.loads(msg["result"]["content"][0]["text"])
    if inner.get("code"):  # e.g. monthly_cap_reached: keyless mode allows only a handful of queries per month
        raise RuntimeError(inner.get("message", inner["code"])[:160])
    return [{"title": i.get("title"), "url": i.get("url"), "snippet": (i.get("content") or "")[:400], "engine": "tavily-keyless"} for i in inner.get("results", [])]


# Snippets are shown in the UI next to the URL; phones, e-mails and street addresses of people are dropped from them
# (the page itself stays one click away for manual verification).
SCRUB = [re.compile(r"\+?\d[\d\s\-()]{8,}\d"), re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), re.compile(r"[\w.*+-]+@[\w*-]+\.[\w*.]+"),
         re.compile(r"(вул\.|вулиця|просп\.|проспект|пров\.|провулок|бульв\.|бульвар)[^;,.]{0,60}(будинок|буд\.|б\.)?[^;,.]{0,30}(квартира|кв\.)?[^;,.]{0,15}", re.I)]


def scrub(t):
    for r in SCRUB:
        t = r.sub("[прибрано]", t or "")
    return t


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
        for name, fn in (("tavily-keyless", lambda: tavily_keyless(q)), ("ddg (best effort)", lambda: ddg(q))):
            try:
                hits = fn()
                engines = [name]
                break
            except Exception as e:  # noqa: BLE001
                print(f"{name} failed:", str(e)[:200])
    # Name queries: keep only pages where the name itself occurs (inflected forms allowed); search engines also return namesakes
    from names import name_matches, CYR
    if len(q.split()) in (2, 3) and all(CYR.match(t) for t in q.split()):
        before = len(hits)
        hits = [h for h in hits if name_matches(q, (h.get("title") or "") + " " + (h.get("snippet") or ""))]
        print(f"name filter: {before} -> {len(hits)}")
    # Snippets are stored as the search engine returns them (quotes of public pages); the project's limits are on what we
    # search BY (never phone, address, tax id of a person), not on quoting a public page.
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
