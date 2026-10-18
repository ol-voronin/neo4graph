"""Local registry worker — runs on a machine with a Ukrainian IP (the cloud cannot reach these sites).

Sources: reyestr.court.gov.ua (full-text / case-number search + decision texts), public-api.nazk.gov.ua (declarations),
ЄДР FOP dump (data/fop.zip, optional). Results are POSTed to the Vercel app (/api/ingest), which writes them to Postgres;
the graph page then shows them next to everything the cloud chain found.

  python3 ua_worker.py --once                     # process pending queue items (GET /api/queue?pending=ua)
  python3 ua_worker.py --query "932/2641/25"      # one query directly (kind auto-detected or --kind)
  python3 ua_worker.py --loop 300                 # poll every 5 min

Config: .env next to this file with API_BASE=https://rhetoric-graph.vercel.app and INGEST_TOKEN=...
Python 3.8+, standard library only. Polite: one request every ~2 s, at most --pages result pages per query.
"""
import argparse
import html
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REYESTR = "https://reyestr.court.gov.ua/"
NAZK = "https://public-api.nazk.gov.ua/v2/documents/list?query={q}&page={p}"
FOP = HERE / "data" / "fop.zip"
CAUSE_RE = re.compile(r"^\d{1,4}/\d{1,6}/\d{2}(?:-[\w/]+)?$")
ORG_RE = re.compile(r"^(тов|пат|прат|ат|тдв|дп|кп|пп|фг|го|бф|ооо|фоп)\b|[«»\"]", re.I)
NAME_RE = re.compile(r"^[^\W\d_][^\W\d_'’-]+(\s+[^\W\d_][^\W\d_'’.-]+){1,2}$")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
opener.addheaders = [("User-Agent", UA)]


def cfg():
    env = {}
    p = HERE / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.setdefault("API_BASE", os.environ.get("API_BASE", "https://rhetoric-graph.vercel.app"))
    env.setdefault("INGEST_TOKEN", os.environ.get("INGEST_TOKEN", ""))
    return env


def classify(q):
    q = q.strip()
    if CAUSE_RE.match(q):
        return "case"
    if re.match(r"^\d{8}$", q):
        return "edrpou"
    if ORG_RE.search(q):
        return "org"
    if NAME_RE.match(q):
        return "person"
    return "text"


def get(url, timeout=60):
    time.sleep(2)
    with opener.open(urllib.request.Request(url), timeout=timeout) as r:
        return r.read()


def post(url, data, headers=None, timeout=120):
    time.sleep(2)
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with opener.open(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- reyestr.court.gov.ua
def reyestr_search(expr="", case_number="", page=1, per_page=50):
    """One result page. Returns (found_total, rows)."""
    if not cj._cookies:
        get(REYESTR)
    fields = {"SearchExpression": expr, "CaseNumber": case_number, "UserCourtCode": "", "ChairmenName": "", "RegNumber": "",
              "RegDateBegin": "", "RegDateEnd": "", "ImportDateBegin": "", "ImportDateEnd": "", "Sort": "1",
              "PagingInfo.ItemsPerPage": str(per_page), "Liga": "true", "ProviderItem": "1"}
    hdr = {"Referer": REYESTR, "Origin": REYESTR.rstrip("/"), "Content-Type": "application/x-www-form-urlencoded"}
    if page == 1:
        s = post(REYESTR, urllib.parse.urlencode(fields).encode(), hdr).decode("utf-8", "ignore")
    else:
        s = get(REYESTR + f"Page/{page}").decode("utf-8", "ignore")
    if "modalcaptcha" in s and 'id="modalcaptcha" style="display: none;"' not in s:
        raise RuntimeError("reyestr asks for a captcha — stop for a while")
    m = re.search(r"знайдено документів:\s*([\d\s]+)", s)
    total = int(re.sub(r"\s", "", m.group(1))) if m else 0
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
        link = re.search(r'href="/Review/(\d+)"', tr)
        if not link:
            continue
        cells = [html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c))).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 7:
            continue
        d = lambda x: ("-".join(reversed(x.split("."))) if re.match(r"^\d{2}\.\d{2}\.\d{4}$", x) else None)  # noqa: E731
        rows.append({"doc_id": link.group(1), "judgment_form": cells[1], "adjudication_date": d(cells[2]), "receipt_date": d(cells[3]),
                     "justice_kind": cells[4], "cause_num": cells[5], "court": cells[6], "judge": cells[7] if len(cells) > 7 else None,
                     "doc_url": f"https://reyestr.court.gov.ua/Review/{link.group(1)}"})
    return total, rows


def reyestr_text(doc_id):
    s = get(f"https://reyestr.court.gov.ua/Review/{doc_id}").decode("utf-8", "ignore")
    m = re.search(r'<textarea id="txtdepository">(.*?)</textarea>', s, re.S)  # the decision (RTF converted to HTML) lives in a textarea
    if not m:
        return None
    t = html.unescape(m.group(1))
    t = re.sub(r"<(br|/p|/div|/tr)[^>]*>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()[:200000]


# ---------------------------------------------------------------- NAZK declarations
def nazk_declarations(name, max_pages=5):
    out, total = [], 0
    for p in range(1, max_pages + 1):
        try:
            d = json.loads(get(NAZK.format(q=urllib.parse.quote(name), p=p)))
        except Exception as e:  # noqa: BLE001
            print("nazk:", e)
            break
        total = d.get("count", 0)
        rows = d.get("data") or []
        for r in rows:
            s1 = (((r.get("data") or {}).get("step_1") or {}).get("data")) or {}
            full = " ".join(x for x in (s1.get("lastname"), s1.get("firstname"), s1.get("middlename")) if x).title()
            out.append({"id": r.get("id"), "year": r.get("declaration_year"), "type": r.get("declaration_type"), "name": full,
                        "work_place": s1.get("workPlace"), "work_post": s1.get("workPost"), "region": s1.get("region") or s1.get("actual_region"),
                        "url": f"https://public.nazk.gov.ua/documents/{r.get('id')}"})
        if len(rows) < 100:
            break
    return total, out


# ---------------------------------------------------------------- ЄДР ФОП dump (optional)
def fop_search(name, limit=60, deadline=None):
    """Scan the ЄДР ФОП dump (470 MB XML in a zip). Takes minutes; stops at the deadline and reports partial=True."""
    if not FOP.exists():
        return None, []
    partial = False
    key = re.sub(r"\s+", " ", name.lower().strip())
    out, total = [], 0
    with zipfile.ZipFile(FOP) as z:
        for n in z.namelist():
            if not n.lower().endswith(".xml"):
                continue
            with z.open(n) as raw:
                for _, el in ET.iterparse(io.BufferedReader(raw, 1 << 20), events=("end",)):
                    if el.tag != "SUBJECT":
                        continue
                    if deadline and time.time() > deadline:
                        partial = True
                        el.clear()
                        break
                    nm = (el.findtext("NAME") or "")
                    if nm.lower().startswith(key):
                        total += 1
                        if len(out) < limit:
                            out.append({"record": el.findtext("RECORD"), "name": nm.title(), "stan": el.findtext("STAN"), "registration": el.findtext("REGISTRATION"),
                                        "terminated": el.findtext("TERMINATED_INFO")})
                    el.clear()
            if partial:
                break
    return (total if not partial else -total - 1), out


# ---------------------------------------------------------------- ingest
def ingest(env, payload):
    req = urllib.request.Request(env["API_BASE"] + "/api/ingest", data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json", "x-ingest-token": env["INGEST_TOKEN"]})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


NO_FOP = False


def process(env, q, kind, pages=2, texts=10, deadline=None):
    notes = []
    hits = []
    tleft = lambda: deadline is None or time.time() < deadline  # noqa: E731

    def push_decisions(rows):
        for i in range(0, len(rows), 25):
            ingest(env, {"kind": "decisions", "rows": rows[i:i + 25]})

    if kind == "case":
        total, rows = reyestr_search(case_number=q)
        p = 2
        while tleft() and len(rows) < total and p <= pages:
            rows += reyestr_search(case_number=q, page=p)[1]
            p += 1
        notes.append(f"ЄДРСР: {total} рішень у справі, завантажено {len(rows)}")
        push_decisions(rows)
        got = 0
        for r in sorted(rows, key=lambda x: (x["judgment_form"] not in ("Вирок", "Рішення", "Постанова"), x["adjudication_date"] or ""))[:texts]:
            if not tleft():
                break
            t = reyestr_text(r["doc_id"])
            if t:
                ingest(env, {"kind": "decisions", "rows": [{**r, "text": t}]})
                got += 1
        notes.append(f"текстів: {got}")
    else:
        exprs = [f'"{q}"'] if kind in ("person", "org") else [q]
        if kind == "person" and len(q.split()) >= 2:
            w = q.split()
            exprs.append(f'"{w[0]} {" ".join(x[0] + "." for x in w[1:])}"')  # Прізвище І.Б.
        seen = set()
        all_rows = []
        for ex in exprs:
            if not tleft():
                break
            total, rows = reyestr_search(expr=ex)
            notes.append(f"ЄДРСР «{ex}»: {total}")
            for r in rows:
                if r["doc_id"] in seen:
                    continue
                seen.add(r["doc_id"])
                all_rows.append(r)
                hits.append({"query": q, "qkind": kind, "source": "reyestr", "ref": r["doc_id"], "url": r["doc_url"],
                             "title": f"{r['judgment_form']} {r['adjudication_date'] or ''} · {r['court']} · {r['cause_num']}",
                             "snippet": f"пошук «{ex}» ({r['justice_kind']})", "payload": r})
        push_decisions(all_rows)
        if kind == "person":
            total, decl = nazk_declarations(q)
            notes.append(f"декларації НАЗК: {total}")
            for d in decl[:60]:
                hits.append({"query": q, "qkind": kind, "source": "nazk_decl", "ref": d["id"], "url": d["url"],
                             "title": f"Декларація {d['year'] or ''} · {d['name']}", "snippet": f"{d['work_place'] or ''} — {d['work_post'] or ''}", "payload": d})
            ftotal, fops = fop_search(q, deadline=deadline) if not NO_FOP else (None, [])
            if ftotal is not None:
                notes.append(f"ЄДР ФОП: {ftotal}" if ftotal >= 0 else f"ЄДР ФОП: {-ftotal - 1}+ (скан не завершено, повторіть)")
                for f in fops:
                    hits.append({"query": q, "qkind": kind, "source": "edr_fop", "ref": f["record"], "url": None, "title": f"ФОП {f['name']}",
                                 "snippet": f"{f['stan'] or ''}; {f['registration'] or ''}", "payload": f})
            else:
                notes.append("ЄДР ФОП: дамп не завантажено")
        got = 0
        for r in all_rows[:min(texts, 5)]:
            if not tleft():
                break
            t = reyestr_text(r["doc_id"])
            if t:
                ingest(env, {"kind": "decisions", "rows": [{**r, "text": t}]})
                got += 1
        notes.append(f"текстів: {got}")
    for i in range(0, len(hits), 50):
        ingest(env, {"kind": "hits", "rows": hits[i:i + 50]})
    return "; ".join(notes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, default=0, help="poll interval seconds")
    ap.add_argument("--query")
    ap.add_argument("--kind")
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--texts", type=int, default=10)
    ap.add_argument("--max-seconds", type=int, default=150, help="time budget per invocation (device shells are short-lived)")
    ap.add_argument("--no-fop", action="store_true", help="skip the ЄДР ФОП scan (slow)")
    a = ap.parse_args()
    global NO_FOP
    NO_FOP = a.no_fop
    env = cfg()
    if not env["INGEST_TOKEN"]:
        sys.exit("INGEST_TOKEN missing (.env)")
    deadline = time.time() + a.max_seconds
    if a.query:
        k = a.kind or classify(a.query)
        print(a.query, "->", k)
        print(process(env, a.query, k, a.pages, a.texts, deadline))
        return
    while True:
        req = urllib.request.Request(env["API_BASE"] + "/api/queue?pending=ua", headers={"x-ingest-token": env["INGEST_TOKEN"]})
        items = json.loads(urllib.request.urlopen(req, timeout=60).read()).get("items", [])
        print(f"pending: {len(items)}")
        for it in items:
            if time.time() > deadline:
                print("time budget spent")
                break
            try:
                note = process(env, it["query"], it["kind"] or classify(it["query"]), a.pages, a.texts, deadline)
                ingest(env, {"kind": "queue", "queue_id": it["id"], "status": "done", "note": note})
                print(it["id"], it["query"], "->", note)
            except Exception as e:  # noqa: BLE001
                ingest(env, {"kind": "queue", "queue_id": it["id"], "status": "error", "note": str(e)[:300]})
                print(it["id"], it["query"], "ERROR", e)
                if "captcha" in str(e):
                    break
        if not a.loop:
            break
        time.sleep(a.loop)
        deadline = time.time() + a.max_seconds


if __name__ == "__main__":
    main()
