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
ERB = HERE / "data" / "erb.zip"   # Єдиний реєстр боржників (Minjust, data.gov.ua), refreshed by the ministry every few days
DUMPS = {FOP: "https://data.gov.ua/dataset/03cc1239-3988-4451-aa0d-aadb77448714/resource/c262938f-cce7-4489-a805-2fd7c5a44e0b/download/fop.zip",
         ERB: "https://data.gov.ua/dataset/783b9b50-faba-4cc9-a393-60485e395b1d/resource/e6ea76c1-01f4-4bd0-a282-7d92d6ecc2a1/download/29-ex_csv_erb.zip"}


def ensure_dumps(max_age_days=7):
    """Download / refresh the open-data dumps the worker greps (ЄДР ФОП, реєстр боржників)."""
    for path, url in DUMPS.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1e7 and time.time() - path.stat().st_mtime < max_age_days * 86400:
            continue
        print("downloading", path.name)
        tmp = path.with_suffix(".part")
        try:
            with opener.open(urllib.request.Request(url), timeout=600) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            tmp.replace(path)
        except Exception as e:  # noqa: BLE001
            print("download failed:", path.name, e)
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


# ---------------------------------------------------------------- court.gov.ua: summons notices (in-absentia proceedings)
SUMMONS_PAGE = "https://court.gov.ua/unknown/"
SUMMONS_API = "https://court.gov.ua/unknown.php"
APOS = str.maketrans({"`": "'", "’": "'", "ʼ": "'"})
_SUMMONS_SESSION = False


def _clean(x):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(x or "")))).strip()


def _nm(x):
    return re.sub(r"\s+", " ", (x or "").translate(APOS)).strip().lower()


def court_summons(name, max_pages=3):
    """«Оголошення про виклик особи в суд та повідомлення про судове рішення» (court.gov.ua/unknown/, no captcha): notices
    courts must publish for parties whose address is unknown or on occupied territory, i.e. in-absentia criminal cases.
    POST /unknown.php with DataTables paging (50 rows); sSearch = the person's name. Keeps notices where a party is this
    person: the same full name for a 3-word query, surname + first name for a 2-word one."""
    global _SUMMONS_SESSION
    q = _nm(name)
    if not _SUMMONS_SESSION:  # the page sets PHPSESSID / SERVERID; the API answers without them too, but behave like the page
        get(SUMMONS_PAGE)
        _SUMMONS_SESSION = True
    hdr = {"Referer": SUMMONS_PAGE, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    out, seen = [], set()
    for page in range(max_pages):
        data = [("sEcho", str(page + 1)), ("iColumns", "10"), ("iDisplayStart", str(page * 50)), ("iDisplayLength", "50"), ("q_ver", "arbitr"),
                ("date", "~"), ("sSearch", name), ("court", "0"), ("region", "0"), ("search_in_participants", "false"), ("a", ""), ("b", ""), ("c", "")]
        try:
            j = json.loads(post(SUMMONS_API, urllib.parse.urlencode(data).encode(), hdr).decode("utf-8", "ignore"))
        except Exception as e:  # noqa: BLE001
            print("summons:", e)
            break
        rows = j.get("aaData") or []
        for r in rows:
            c = (lambda k: r.get(k) if isinstance(r, dict) else (r[int(k)] if int(k) < len(r) else ""))
            parties = _clean(c("5"))
            mine = None
            for part in re.split(r",\s*(?=[^,:]{2,40}:)", parties):
                role, _, who = part.partition(":")
                w = _nm(who)
                if (len(q.split()) >= 3 and w == q) or (len(q.split()) == 2 and (w == q or w.startswith(q + " "))):
                    mine = (role.strip(), who.strip())
                    break
            if not mine:
                continue
            raw8 = str(c("8") or "")
            fid = re.search(r'FILENAME"?\s+CONTENT="?([\w.]+?)(?:\.html)?"', html.unescape(raw8), re.I)
            text = _clean(re.sub(r"(?is)<(head|style|script)[^>]*>.*?</\1>", " ", html.unescape(raw8)))
            text = re.sub(r"^(Оголошення про виклик|Повідомлення про (судове )?рішення)\s*", "", text)
            cause = _clean(re.sub(r"(?is)<form.*?</div>", " ", str(c("4"))))
            cause = (re.search(r"\d{1,4}/\d{1,7}/\d{2}(?:-[\w/]+)?", cause) or [cause])[0]
            court_code = (re.search(r'name="UserCourtCode"\s+value="(\d+)"', str(c("4"))) or [None, None])[1]
            erdr = sorted(set(re.findall(r"(?<!\d)\d{17}(?!\d)", text)))
            nid = fid.group(1) if fid else f"{court_code}:{cause}:{_clean(c('0'))}"
            if nid in seen:
                continue
            seen.add(nid)
            out.append({"id": nid, "name": mine[1], "role": mine[0], "court": _clean(c("1")), "court_code": court_code, "cause_num": cause,
                        "proc_num": erdr[0] if erdr else None, "proc_nums": erdr, "date": _clean(c("0")), "hearing": _clean(c("2")),
                        "judges": _clean(c("3")), "parties": parties[:600], "category": _clean(c("7")),
                        "notice_type": "Оголошення про виклик" if "виклик" in raw8[:200].lower() else _clean(raw8[:120]).split(" ")[0],
                        "text": text[:1500], "url": SUMMONS_PAGE})
        if len(rows) < 50:
            break
    return out


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


NAZK_DOC = "https://public-api.nazk.gov.ua/v2/documents/{id}"
HIDDEN = re.compile(r"^\[(Конфіденційна інформація|Не застосовується|Не відомо|Член сім'ї не надав інформацію)\]$|^$")
# KATOTTG oblast code (2 digits after "UA") -> alert-map slug used across the project
KATOTTG = {"01": "crimea", "05": "vinnytska", "07": "volynska", "12": "dnipropetrovska", "14": "donetska", "18": "zhytomyrska", "21": "zakarpatska",
           "23": "zaporizka", "26": "ivano-frankivska", "32": "kyivska", "35": "kirovohradska", "44": "luhanska", "46": "lvivska", "48": "mykolaivska",
           "51": "odeska", "53": "poltavska", "56": "rivnenska", "59": "sumska", "61": "ternopilska", "63": "kharkivska", "65": "khersonska",
           "68": "khmelnytska", "71": "cherkaska", "73": "chernivetska", "74": "chernihivska", "80": "kyiv-city", "85": "crimea"}
STEP_KIND = {"step_2": "family", "step_3": "real_estate", "step_4": "unfinished", "step_5": "movable", "step_6": "vehicle", "step_7": "securities",
             "step_8": "corporate", "step_9": "beneficial", "step_10": "intangible", "step_11": "income", "step_12": "money", "step_13": "liability",
             "step_14": "expense", "step_16": "membership", "step_17": "account"}


def _v(x):
    x = "" if x is None else str(x).strip()
    return "" if HIDDEN.match(x) else x


def _people(o):
    """every «<prefix>lastname / firstname / middlename» triple in a dict, nested lists and dicts included"""
    out = []
    if isinstance(o, dict):
        for k in o:
            if k.endswith("lastname"):
                p = k[:-len("lastname")]
                full = " ".join(x for x in (_v(o.get(k)), _v(o.get(p + "firstname")), _v(o.get(p + "middlename"))) if x)
                if len(full.split()) >= 2:
                    out.append(full)
        for v in o.values():
            if isinstance(v, (dict, list)):
                out += _people(v)
    elif isinstance(o, list):
        for v in o:
            out += _people(v)
    return out


def _orgs(o):
    """(name, ЄДРПОУ) pairs: «…company_name» + «…company_code», «name» + «…company_code» / «reestrCode», «objectName» + «reestrCode»"""
    out = []
    if isinstance(o, dict):
        for k in o:
            if k.endswith("company_name") or k in ("name", "objectName", "emitent_ua_company_name"):
                p = k[:-len("company_name")] if k.endswith("company_name") else ""
                code = _v(o.get(p + "company_code")) or _v(o.get("corporate_rights_company_code")) or _v(o.get("beneficial_owner_company_code")) or _v(o.get("reestrCode"))
                if _v(o.get(k)):
                    out.append((_v(o.get(k)), code if re.fullmatch(r"\d{8}", code or "") else None))
        for v in o.values():
            if isinstance(v, (dict, list)):
                out += _orgs(v)
    elif isinstance(o, list):
        for v in o:
            out += _orgs(v)
    return out


def _region(it):
    m = re.search(r"UA(\d{2})", str(it.get("regionPath") or it.get("cityPath") or ""))
    return KATOTTG.get(m.group(1)) if m else None


def _num(x):
    x = _v(x).replace(" ", "").replace(",", ".")
    try:
        return float(x)
    except ValueError:
        return None


def decl_items(doc, query):
    """A full NAZK document -> rows for decl_items (migration 023). Owners: rightBelongs "1" = declarant, a family member's
    iteration id = that member, "j" = a third person named in the right itself."""
    D = doc.get("data") or {}
    s1 = (D.get("step_1") or {}).get("data") or {}
    declarant = " ".join(x for x in (_v(s1.get("lastname")), _v(s1.get("firstname")), _v(s1.get("middlename"))) if x).lower()
    fam = {}
    for m in (D.get("step_2") or {}).get("data") or []:
        if isinstance(m, dict):
            nm = " ".join(x for x in (_v(m.get("lastname")), _v(m.get("firstname")), _v(m.get("middlename"))) if x)
            if nm:
                fam[str(m.get("iteration") or m.get("id") or "")] = (nm.lower(), _v(m.get("subjectRelation")))
    rows, idx = [], 0

    def add(step, it, label, **kw):
        nonlocal idx
        idx += 1
        rows.append({"decl_id": doc.get("id"), "idx": idx, "query": query.lower(), "declarant": declarant, "year": doc.get("declaration_year"),
                     "decl_type": doc.get("declaration_type"), "step": step, "kind": STEP_KIND.get(step, step), "label": label[:300],
                     "owner": kw.get("owner"), "rel_name": (kw.get("rel_name") or None) and kw["rel_name"][:300], "rel_code": kw.get("rel_code"),
                     "rel_kind": kw.get("rel_kind"), "value": kw.get("value"), "currency": kw.get("currency"), "since": kw.get("since"),
                     "region": kw.get("region"), "details": {k: v for k, v in it.items() if isinstance(v, (str, int, float)) and _v(v) and not k.endswith("extendedstatus")
                                                             and k != "iteration"} if isinstance(it, dict) else {}})

    def owner_of(it):
        for r in (it.get("rights") or []) if isinstance(it, dict) else []:
            b = str(r.get("rightBelongs") or "")
            if b == "1":
                return declarant
            if b in fam:
                return fam[b][0]
        return declarant

    for iid, (nm, rel) in fam.items():
        add("step_2", {}, f"{rel or 'член сімʼї'}: {nm.title()}", owner=declarant, rel_name=nm, rel_kind="person")
    for step in sorted(D, key=lambda x: int(x.split("_")[1]) if x.split("_")[1].isdigit() else 0):
        if step in ("step_0", "step_1", "step_2", "step_15"):
            continue
        data = (D.get(step) or {}).get("data")
        items = data if isinstance(data, list) else [x for v in (data or {}).values() if isinstance(v, list) for x in v] if isinstance(data, dict) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            ot = _v(it.get("objectType")) or _v(it.get("specExpensesSubject")) or ""
            own = owner_of(it)
            if step in ("step_3", "step_4"):
                area = _v(it.get("totalArea"))
                city = _v(it.get("city")) or _v(it.get("ua_cityName"))
                add(step, it, " ".join(x for x in (ot or "Нерухомість", f"{area} м²" if area else "", city) if x), owner=own, region=_region(it),
                    value=_num(it.get("costDate") or it.get("cost_date_assessment") or it.get("costAssessment")), since=_v(it.get("owningDate")))
            elif step == "step_6":
                add(step, it, " ".join(x for x in (_v(it.get("brand")), _v(it.get("model")), _v(it.get("graduationYear"))) if x) or ot or "Транспортний засіб",
                    owner=own, value=_num(it.get("costDate")), since=_v(it.get("owningDate")), rel_name=ot)
            elif step in ("step_5", "step_10"):
                add(step, it, " ".join(x for x in (ot, _v(it.get("propertyDescr")) or _v(it.get("descriptionObject")), _v(it.get("trademark"))) if x)[:300] or "Майно",
                    owner=own, since=_v(it.get("owningDate")))
            elif step in ("step_7", "step_8", "step_9"):
                for nm, code in (_orgs(it) or [(ot, None)]):
                    add(step, it, nm, owner=own, rel_name=nm, rel_code=code, rel_kind="org", value=_num(it.get("cost") or it.get("amount")), since=_v(it.get("owningDate")))
            elif step in ("step_11", "step_12", "step_13", "step_17"):
                size = _num(it.get("sizeIncome") or it.get("sizeAssets") or it.get("sizeObligation"))
                cur = _v(it.get("assetsCurrency") or it.get("currency"))
                parties = [(n, c, "org") for n, c in _orgs(it)] + [(p, None, "person") for p in _people(it) if p.lower() != declarant]
                for nm, code, rk in (parties or [(None, None, None)]):
                    add(step, it, ot or STEP_KIND[step], owner=own, rel_name=nm, rel_code=code, rel_kind=rk, value=size, currency=cur or None)
            elif step == "step_14":
                add(step, it, " ".join(x for x in (_v(it.get("specExpenses")), _v(it.get("specExpensesSubject"))) if x) or "Видаток", owner=own,
                    since=_v(it.get("transactionDate")), value=_num(it.get("costAmount") or it.get("cost")))
            elif step == "step_16":
                for nm, code in _orgs(it):
                    add(step, it, f"{_v(it.get('unitName')) or 'член'}: {nm}", owner=own, rel_name=nm, rel_code=code, rel_kind="org")
    return rows


def nazk_document(doc_id):
    return json.loads(get(NAZK_DOC.format(id=doc_id)))


def pick_declarations(q, decl, max_docs=5):
    """Declarations of this very person: the full name must match a 3-word query; a 2-word query only when every hit carries
    the same full name (otherwise namesakes would be merged). Latest per year, annual first; at most max_docs."""
    ql = q.lower().strip()
    names = {(d.get("name") or "").lower() for d in decl}
    if len(ql.split()) >= 3:
        mine = [d for d in decl if (d.get("name") or "").lower() == ql]
    else:
        mine = decl if len(names) == 1 and next(iter(names)).startswith(ql) else []
    by_year = {}
    for d in sorted(mine, key=lambda d: (d.get("type") != 1, -(d.get("year") or 0))):
        by_year.setdefault(d.get("year"), d)
    return [by_year[y] for y in sorted(by_year, key=lambda y: -(y or 0))][:max_docs]


# ---------------------------------------------------------------- ЄДР ФОП dump (optional)

# ---------------------------------------------------------------- data lake (Parquet + DuckDB), replaces per-query grep
LAKE = HERE / "data" / "lake"
LAKE_URL = "https://github.com/ol-voronin/neo4graph/releases/download/lake-latest/"
LAKE_TABLES = ("fop", "erb", "uo", "people_roles")


def lake_sync(max_age_h=26):
    """Fetch the nightly Parquet tables (built in GitHub Actions) when missing or older than a day."""
    LAKE.mkdir(parents=True, exist_ok=True)
    for t in LAKE_TABLES:
        p = LAKE / f"{t}.parquet"
        if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
            continue
        try:
            tmp = p.with_suffix(".part")
            with urllib.request.urlopen(urllib.request.Request(LAKE_URL + p.name, headers={"User-Agent": UA}), timeout=900) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 22):
                    f.write(chunk)
            tmp.replace(p)
            print(f"lake: {p.name} {p.stat().st_size / 1e6:.0f} MB")
        except Exception as e:  # noqa: BLE001
            print("lake sync failed:", p.name, e)


_LAKE_CON = None


def lake_con():
    global _LAKE_CON
    if _LAKE_CON is None:
        try:
            import duckdb
        except ImportError:
            return None
        con = duckdb.connect()
        ok = False
        for t in LAKE_TABLES:
            p = LAKE / f"{t}.parquet"
            if p.exists():
                con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{p}')")
                ok = True
        _LAKE_CON = con if ok else False
    return _LAKE_CON or None


def lake_has(t):
    return (LAKE / f"{t}.parquet").exists() and lake_con() is not None


def name_key(s):
    s = (s or "").upper().replace("Ё", "Е").replace("’", "'").replace("`", "'").replace('"', "").replace("«", "").replace("»", "")
    return re.sub(r"\s+", " ", s).strip()


def lake_rows(sql, args):
    cur = lake_con().execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def lake_roles(name, limit=60):
    """Person -> companies where they are founder / beneficiary / signer (ЄДР юросіб, from the lake)."""
    if not lake_has("people_roles"):
        return None, []
    rows = lake_rows("SELECT r.name, r.role, r.edrpou, r.detail, u.short_name, u.name AS org_name, u.stan FROM people_roles r "
                     "LEFT JOIN uo u USING (edrpou) WHERE r.name_key LIKE ? LIMIT ?", [name_key(name) + "%", limit])
    return len(rows), rows


def fop_search(name, limit=60, deadline=None):
    """ЄДР ФОП dump: one <SUBJECT> per line, windows-1251, ~5.3 GB unpacked. `unzip -p | grep` scans it in ~25 s;
    the Python fallback (iterparse) is used only when those tools are missing and honours the deadline (partial=True)."""
    if lake_has("fop"):
        rows = lake_rows("SELECT record, name, stan, registration, terminated FROM fop WHERE name_key LIKE ?", [name_key(name) + "%"])
        return len(rows), rows[:limit]
    if not FOP.exists():
        return None, []
    key = re.sub(r"\s+", " ", name.strip()).upper()
    out, total = [], 0

    def rec(line):
        g = lambda tag: html.unescape((re.search(f"<{tag}>(.*?)</{tag}>", line) or [None, ""])[1])  # noqa: E731
        return {"record": g("RECORD"), "name": g("NAME").title(), "stan": g("STAN"), "registration": g("REGISTRATION"), "terminated": g("TERMINATED_INFO")}

    import shutil
    import subprocess
    if shutil.which("unzip") and shutil.which("grep"):
        pat = ("<NAME>" + key).encode("cp1251", "ignore")
        p1 = subprocess.Popen(["unzip", "-p", str(FOP)], stdout=subprocess.PIPE)
        p2 = subprocess.Popen(["grep", "-a", "-F", pat], stdin=p1.stdout, stdout=subprocess.PIPE, env={"LC_ALL": "C"})
        p1.stdout.close()
        for raw in p2.stdout:
            line = raw.decode("cp1251", "ignore")
            total += 1
            if len(out) < limit:
                out.append(rec(line))
        p2.wait()
        return total, out
    partial = False
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
                    if nm.upper().startswith(key):
                        total += 1
                        if len(out) < limit:
                            out.append({"record": el.findtext("RECORD"), "name": nm.title(), "stan": el.findtext("STAN"), "registration": el.findtext("REGISTRATION"),
                                        "terminated": el.findtext("TERMINATED_INFO")})
                    el.clear()
            if partial:
                break
    return (total if not partial else -total - 1), out


def grep_dump(path, pattern_utf8):
    """Stream a zipped cp1251 dump through grep (fast); yields decoded matching lines."""
    import shutil
    import subprocess
    if not (shutil.which("unzip") and shutil.which("grep")):
        return
    pat = pattern_utf8.encode("cp1251", "ignore")
    p1 = subprocess.Popen(["unzip", "-p", str(path)], stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["grep", "-a", "-F", pat], stdin=p1.stdout, stdout=subprocess.PIPE, env={"LC_ALL": "C"})
    p1.stdout.close()
    for raw in p2.stdout:
        yield raw.decode("cp1251", "ignore")
    p2.wait()


def erb_search(name, limit=60):
    """Єдиний реєстр боржників: debtors by name (persons are Title Case, companies upper case). Birth date is reduced to the
    year; the debtor code column is kept only when it is an 8-digit ЄДРПОУ (never a person's tax number)."""
    if lake_has("erb"):
        rows = lake_rows("SELECT name, birth_date, code, publisher, executor, vp, category FROM erb WHERE name_key LIKE ?", [name_key(name) + "%"])
        return len(rows), [{"name": r["name"], "birth_year": (r["birth_date"] or "")[-4:] or None, "edrpou": r["code"] if re.fullmatch(r"\d{8}", r["code"] or "") else None,
                            "publisher": r["publisher"], "executor": r["executor"], "vp": r["vp"], "category": r["category"]} for r in rows[:limit]]
    if not ERB.exists():
        return None, []
    import csv
    key = re.sub(r"\s+", " ", name.strip())
    out, total = [], 0
    for variant in {key, key.title(), key.upper()}:
        for line in grep_dump(ERB, '"' + variant):
            try:
                r = next(csv.reader([line]))
            except Exception:  # noqa: BLE001
                continue
            if len(r) < 11 or not r[0].lower().startswith(key.lower()):
                continue
            total += 1
            if len(out) < limit:
                out.append({"name": r[0], "birth_year": r[1][-4:] if r[1] else None, "edrpou": r[2] if re.fullmatch(r"\d{8}", r[2] or "") else None,
                            "publisher": r[3], "executor": r[4], "vp": r[9], "category": r[10]})
    return total, out


# ---------------------------------------------------------------- YouControl public FOP page (КВЕД etc.)
def yc_fop(name, limit=5):
    """YouControl's public ФОП pages carry what the open ЄДР dump no longer has: КВЕД (main + others), registration date,
    status and city. Only exact full-name matches are taken; street/house is not kept (city level only)."""
    try:
        page = get("https://youcontrol.com.ua/search/?" + urllib.parse.urlencode({"q": name})).decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        print("youcontrol search failed:", e)
        return []
    out = []
    key = re.sub(r"\s+", " ", name.strip()).upper()
    for yid in dict.fromkeys(re.findall(r"fop_details/(\d+)", page)):
        if len(out) >= limit:
            break
        try:
            h = get(f"https://youcontrol.com.ua/catalog/fop_details/{yid}").decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            continue
        t = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
        t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n\s*\n+", "\n", t)
        m = re.search(r"ФОП ([А-ЯІЇЄҐ'’\- ]+)\n", t)
        full = (m.group(1).strip() if m else "").upper()
        if not full.startswith(key):
            continue
        kv = re.search(r"Основний:\s*\n\s*(\d{2}\.\d{2} [^\n]+)", t)
        others = re.findall(r"\n\s*(\d{2}\.\d{2} [^\n]+)", t.split("Інші:", 1)[1].split("Адреса", 1)[0]) if "Інші:" in t else []
        reg = re.search(r"Дата реєстрації\s*\n\s*(\d{2}\.\d{2}\.\d{4})", t)
        st = re.search(r"відзначається як\s*—\s*([^\n|]+)", t)
        addr = re.search(r"Адреса:\s*\n\s*Україна,\s*\n[^\n]*\n([^\n]*)", t)
        city = ""
        if addr:
            parts = [x.strip() for x in addr.group(1).split(",")]
            city = ", ".join(x for x in parts if re.search(r"обл\.|місто|м\.|район|смт|село", x))[:80]
        out.append({"yc_id": yid, "name": full.title(), "kved_main": kv.group(1).strip() if kv else None, "kved_other": [x.strip() for x in others][:12],
                    "registration": reg.group(1) if reg else None, "status": st.group(1).strip() if st else None, "city": city,
                    "url": f"https://youcontrol.com.ua/catalog/fop_details/{yid}"})
    return out


def yc_company(q, kind="org", limit=3):
    """YouControl's public company page: what the open ЄДР dump lacks — КВЕД (main + others), head (name, since, role),
    legal address (as shown: YouControl masks house numbers), status, capital, registration date, counts of licences /
    trade marks / vehicles / court documents, VAT status. Company data only; founders' home addresses are not kept."""
    def page_text(u):
        h = get(u).decode("utf-8", "ignore")
        t = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
        t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
        t = re.sub(r"[ \t\xa0]+", " ", t)
        return re.sub(r"\n\s*\n+", "\n", t)
    key = re.sub(r"[«»\"'’]", "", q).strip().lower()
    key = re.sub(r"^(тов|пат|прат|ат|тдв|дп|кп|пп|фг|го|бф|ооо|llc|ltd)\s+", "", key).strip()
    if kind == "edrpou":
        ids = [q]
    else:
        try:
            page = get("https://youcontrol.com.ua/search/?" + urllib.parse.urlencode({"q": key})).decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            print("youcontrol search failed:", e)
            return []
        ids = list(dict.fromkeys(re.findall(r"company_details/(\d{8})", page)))
    out = []
    for cid in ids:
        if len(out) >= limit:
            break
        try:
            t = page_text(f"https://youcontrol.com.ua/catalog/company_details/{cid}/")
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"Юридичній особі \"?([^\n\"]+?)\"? присвоєно код ЄДРПОУ (\d{8})", t)
        short = m.group(1).strip() if m else ""
        m2 = re.search(r"судові рішення, в яких згадується юридична особа ([^\n,]+?),", t)
        full = m2.group(1).strip() if m2 else ""
        if kind != "edrpou" and key and key not in (short + " " + full).lower():
            continue
        def grab(pat, flags=0):
            mm = re.search(pat, t, flags)
            return mm.group(1).strip() if mm else None
        head = re.search(r"Уповноважені особи\s*\n\s*Детальніше\s*\n\s*([А-ЯІЇЄҐ'’\- ]+)\n\s*(\d{2}\.\d{2}\.\d{4})?,?\s*([^\n]*)", t)
        others = []
        if "Інші:" in t:
            for ln in t.split("Інші:", 1)[1].split("\n")[1:40]:
                if re.match(r"\s*\d{2}\.\d{2} ", ln):
                    others.append(ln.strip())
                elif others:
                    break
        assets = grab(r"(зареєстровано \d+ торгов\w+ мар\w+, \d+ одиниц\w+ автотранспорту[^.]*ліценз\w+)")
        out.append({"edrpou": cid, "name": full or short, "short_name": short, "status": grab(r"Статус з ЄДР\s*\n\s*([^\n]+)"),
                    "registration": grab(r"Дата реєстрації\s*\n\s*(\d{2}\.\d{2}\.\d{4})"), "opf": grab(r"Організаційно-правова форма\s*\n([^\n]+)"),
                    "capital": grab(r"Розмір статутного капіталу\s*\n\s*([^\n]+)"), "kved_main": grab(r"Основний:\s*\n\s*(\d{2}\.\d{2} [^\n]+)"),
                    "kved_other": others[:15], "head": head.group(1).strip().title() if head else None,
                    "head_since": head.group(2) if head and head.group(2) else None, "head_role": (head.group(3).strip(" ,") if head else None),
                    "address": grab(r"Місцезнаходження юридичної особи:\s*\n\s*([^\n]+)"), "vat": grab(r"Реєстр платників ПДВ\s*\n[^\n]*\n\s*([^\n]+)"),
                    "assets": assets, "court_docs": grab(r"згадується в (\d+) судових документах"),
                    "url": f"https://youcontrol.com.ua/catalog/company_details/{cid}/"})
    return out


# ---------------------------------------------------------------- ingest
DRY = False


def ingest(env, payload):
    if DRY:
        rows = payload.get("rows") or []
        print(f"[dry] {payload.get('kind')}: {len(rows)} rows" + "".join(f"\n    {r.get('source', '')} {r.get('title') or r.get('cause_num') or ''} | {(r.get('snippet') or '')[:140]}"
                                                                    for r in rows[:25] if payload.get("kind") in ("hits", "decisions")))
        return {"ok": True, "dry": True}
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
        exprs = [f'"{q}"'] if kind in ("person", "org", "edrpou") else [q]
        # no «Прізвище І.Б.» search: the register does not honour the phrase with initials and returned 121 k unrelated
        # decisions for «Сальдо В. В.»; full name only (plus the cases found through summons notices below)
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
            # contents of the person's own declarations: real estate, vehicles, companies, income sources, loans, family
            items = []
            for d in pick_declarations(q, decl):
                if not tleft():
                    break
                try:
                    items += decl_items(nazk_document(d["id"]), q)
                except Exception as e:  # noqa: BLE001
                    print("nazk doc:", d["id"], e)
                time.sleep(1)
            for i in range(0, len(items), 100):
                ingest(env, {"kind": "decl_items", "rows": items[i:i + 100]})
            notes.append(f"вміст декларацій: {len(items)} записів")
            ftotal, fops = fop_search(q, deadline=deadline) if (not NO_FOP or lake_has("fop")) else (None, [])
            if ftotal is not None:
                notes.append(f"ЄДР ФОП: {ftotal}" if ftotal >= 0 else f"ЄДР ФОП: {-ftotal - 1}+ (скан не завершено, повторіть)")
                for f in fops:
                    hits.append({"query": q, "qkind": kind, "source": "edr_fop", "ref": f["record"], "url": None, "title": f"ФОП {f['name']}",
                                 "snippet": f"{f['stan'] or ''}; {f['registration'] or ''}", "payload": f})
            else:
                notes.append("ЄДР ФОП: дамп не завантажено")
        if kind == "person" and len(q.split()) >= 2 and tleft():
            # summons notices -> the person's cases (mostly in-absentia criminal ones) -> decisions and texts in those cases
            summ = court_summons(q)
            notes.append(f"оголошення про виклик: {len(summ)}")
            for n in summ:
                hits.append({"query": q, "qkind": kind, "source": "summons", "ref": n["id"], "url": n["url"],
                             "title": f"Виклик до суду · {n['court']} · справа {n['cause_num']}",
                             "snippet": f"{n['role']}; {n['category']}; засідання {n['hearing']}" + (f"; ЄРДР {n['proc_num']}" if n["proc_num"] else ""), "payload": n})
            causes = list(dict.fromkeys(n["cause_num"] for n in summ if re.match(r"^\d{1,4}/\d+/\d{2}", n["cause_num"] or "")))[:5]
            case_rows = []
            for cn in causes:
                if not tleft():
                    break
                try:
                    total, rows = reyestr_search(case_number=cn)
                except Exception as e:  # noqa: BLE001
                    notes.append(f"ЄДРСР {cn}: {e}")
                    continue
                notes.append(f"ЄДРСР справа {cn}: {total}")
                for r in rows:
                    if r["doc_id"] in seen:
                        continue
                    seen.add(r["doc_id"])
                    case_rows.append(r)
                    hits.append({"query": q, "qkind": kind, "source": "reyestr", "ref": r["doc_id"], "url": r["doc_url"],
                                 "title": f"{r['judgment_form']} {r['adjudication_date'] or ''} · {r['court']} · {r['cause_num']}",
                                 "snippet": f"справа з оголошення про виклик ({r['justice_kind']})", "payload": r})
            push_decisions(case_rows)
            # texts: rulings first (arrests of property are «Ухвала»), newest first; up to 5
            got = arrests = 0
            for r in sorted(case_rows, key=lambda x: (x["judgment_form"] != "Ухвала", -int((x["adjudication_date"] or "0000").replace("-", "")[:8] or 0)))[:5]:
                if not tleft():
                    break
                t = reyestr_text(r["doc_id"])
                if t:
                    ingest(env, {"kind": "decisions", "rows": [{**r, "text": t}]})
                    got += 1
                    arrests += bool(re.search(r"арешт\w* на (майно|автомоб|транспорт|квартир|нерухом|грошов|кошти)", t, re.I))
            if causes:
                notes.append(f"тексти рішень у справах з викликів: {got} (з арештом майна: {arrests})")
        if kind == "person":
            rtotal, roles = lake_roles(q)
            if rtotal is not None:
                notes.append(f"ЄДР ролі в компаніях: {rtotal}")
                for r in roles:
                    hits.append({"query": q, "qkind": kind, "source": "edr_role", "ref": f"{r['edrpou']}:{r['role']}:{name_key(r['name'])}", "url": None,
                                 "title": f"{ {'founder': 'Засновник', 'beneficiary': 'Бенефіціар', 'signer': 'Підписант/керівник'}[r['role']] } · {r['short_name'] or r['org_name'] or r['edrpou']}",
                                 "snippet": f"{r['name']} — {r['detail'][:200]}; ЄДРПОУ {r['edrpou']}; {r['stan'] or ''}", "payload": r})
        if kind == "person" and len(q.split()) >= 3:
            for y in yc_fop(q):
                hits.append({"query": q, "qkind": kind, "source": "yc_fop", "ref": y["yc_id"], "url": y["url"],
                             "title": f"ФОП {y['name']} · КВЕД {y['kved_main'] or '?'}", "snippet": f"{y['status'] or ''}; реєстрація {y['registration'] or '?'}; {y['city']}", "payload": y})
            notes.append(f"YouControl ФОП: {sum(1 for h in hits if h['source'] == 'yc_fop')}")
        if kind in ("org", "edrpou"):
            for c in yc_company(q, kind):
                hits.append({"query": q, "qkind": kind, "source": "yc_org", "ref": c["edrpou"], "url": c["url"],
                             "title": f"{c['short_name'] or c['name']} · {c['edrpou']} · КВЕД {(c['kved_main'] or '?')[:5]}",
                             "snippet": f"{c['status'] or ''}; реєстрація {c['registration'] or '?'}; керівник: {c['head'] or '?'}; {c['kved_main'] or ''}", "payload": c})
            notes.append(f"YouControl компанія: {sum(1 for h in hits if h['source'] == 'yc_org')}")
        if kind in ("person", "org", "edrpou"):
            etotal, debtors = erb_search(q)
            if etotal is not None:
                notes.append(f"реєстр боржників: {etotal}")
                for d in debtors:
                    hits.append({"query": q, "qkind": kind, "source": "erb", "ref": d["vp"], "url": "https://erb.minjust.gov.ua/",
                                 "title": f"Боржник · {d['category'] or 'провадження'} · {d['name']}" + (f" ({d['birth_year']} р.н.)" if d["birth_year"] else ""),
                                 "snippet": f"{d['publisher'] or ''}; виконавець: {d['executor'] or ''}; ВП № {d['vp']}", "payload": d})
            else:
                notes.append("реєстр боржників: дамп не завантажено")
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
    ap.add_argument("--dry-run", action="store_true", help="--query: print what would be sent to /api/ingest instead of sending it")
    a = ap.parse_args()
    global NO_FOP, DRY
    NO_FOP = a.no_fop
    DRY = a.dry_run
    env = cfg()
    if not env["INGEST_TOKEN"]:
        sys.exit("INGEST_TOKEN missing (.env)")
    deadline = time.time() + a.max_seconds
    try:
        import duckdb  # noqa: F401
        lake_sync()          # Parquet tables: lookups in < 1 s
    except ImportError:
        print("duckdb missing (pip3 install duckdb) — falling back to grep over zipped dumps")
        if not a.no_fop:
            ensure_dumps()
    if a.query:
        k = a.kind or classify(a.query)
        print(a.query, "->", k)
        print(process(env, a.query, k, a.pages, a.texts, deadline))
        return
    while True:
        try:
            ingest(env, {"kind": "heartbeat", "note": f"loop={a.loop}s fop={'yes' if FOP.exists() else 'no'}"})
        except Exception as e:  # noqa: BLE001
            print("heartbeat failed:", e)
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
