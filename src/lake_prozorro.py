"""ProZorro contracts -> lake table pz_contracts (one row per contract x supplier), incremental, from the official CDB API.

Why: ProZorro is the only official open source that publishes a ФОП's РНОКПП (supplier identifier, scheme UA-EDR, 10 digits)
together with an address. Suppliers and buyers of signed contracts are public by law (Закон «Про публічні закупівлі», ст. 10).

The contracts feed (/api/2.5/contracts, ~20k changed contracts per day) lists ids only; suppliers need one GET per contract.
8 keep-alive connections give 15-30 contracts/s (16 fresh connections got HTTP 429): a night handles the day's changes
in ~15 min and spends the rest of --minutes walking history backwards from the first run (~60k contracts per hour,
so the full history fills in over months; a manual run with a larger PROZORRO_MINUTES speeds it up).
Two cursors are kept in prozorro_state.json:
  fwd  — ascending feed offset: everything changed since the last run;
  back — descending feed offset: history not yet seen (null once the feed is exhausted).

  python src/lake_prozorro.py [--minutes 60] [--workers 8]

Privacy: for individuals (10-digit codes) only region, locality and postal code are kept, never street/house/flat.
"""
import argparse
import http.client
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAKE = Path(os.environ.get("LAKE_DIR", ROOT / "data" / "lake"))
API = "https://public-api.prozorro.gov.ua/api/2.5"
UA = {"User-Agent": "Mozilla/5.0 (research; neo4graph lake; github.com/ol-voronin/neo4graph)", "accept": "application/json"}
STATE = LAKE / "prozorro_state.json"
OUT = LAKE / "pz_contracts.parquet"
PARTS = LAKE / "pz_parts"
PAGE = 100          # feed page size: one page is fetched in parallel, then the cursor advances
CHECKPOINT = 2000   # contracts per part file; state is saved together with each part

COLS = ["contract_id", "contract_no", "tender_id", "status", "date_signed", "date_modified", "amount", "currency",
        "buyer_code", "buyer_name", "buyer_region", "buyer_locality", "buyer_street",
        "supplier_code", "supplier_scheme", "supplier_name", "supplier_region", "supplier_locality", "supplier_street", "supplier_postal"]


_local = threading.local()
HOST = "public-api.prozorro.gov.ua"


def get(url, tries=6):
    """GET JSON over a per-thread keep-alive connection (fewer new connections: fewer 429s); retries 429/5xx with backoff."""
    path = url.split(HOST, 1)[1]
    for i in range(tries):
        try:
            c = getattr(_local, "conn", None) or http.client.HTTPSConnection(HOST, timeout=60)
            _local.conn = c
            c.request("GET", path, headers=UA)
            r = c.getresponse()
            body = r.read()
            if r.status == 200:
                return json.loads(body)
            if r.status == 404:
                return None
            if r.status not in (429, 500, 502, 503, 504) or i == tries - 1:
                raise RuntimeError(f"{r.status} {url}")
            time.sleep(float(r.getheader("Retry-After") or 0) or 2 ** i)
        except (http.client.HTTPException, OSError):
            _local.conn = None
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def _addr(a):
    a = a or {}
    return (a.get("region") or "").strip(), (a.get("locality") or "").strip(), (a.get("streetAddress") or "").strip(), (a.get("postalCode") or "").strip()


def rows_for(cid):
    d = (get(f"{API}/contracts/{cid}") or {}).get("data")
    if not d:
        return []
    buyer = d.get("buyer") or d.get("procuringEntity") or {}
    br, bl, bs, _ = _addr(buyer.get("address"))
    val = d.get("value") or {}
    out = []
    for s in d.get("suppliers") or []:
        ident = s.get("identifier") or {}
        code = str(ident.get("id") or "").strip()
        r, loc, street, postal = _addr(s.get("address"))
        if len(code) == 10 and code.isdigit():  # individual (ФОП): РНОКПП is published, the home address is not kept
            street = ""
        out.append({"contract_id": d.get("id"), "contract_no": d.get("contractID"), "tender_id": d.get("tender_id"), "status": d.get("status"),
                    "date_signed": (d.get("dateSigned") or "")[:10], "date_modified": (d.get("dateModified") or "")[:19],
                    "amount": float(val.get("amount") or 0) or None, "currency": val.get("currency"),
                    "buyer_code": str((buyer.get("identifier") or {}).get("id") or ""), "buyer_name": buyer.get("name") or (buyer.get("identifier") or {}).get("legalName"),
                    "buyer_region": br, "buyer_locality": bl, "buyer_street": bs,
                    "supplier_code": code, "supplier_scheme": ident.get("scheme"), "supplier_name": s.get("name") or ident.get("legalName"),
                    "supplier_region": r, "supplier_locality": loc, "supplier_street": street, "supplier_postal": postal})
    return out


def write_part(rows):
    import pyarrow as pa
    import pyarrow.parquet as pq
    PARTS.mkdir(parents=True, exist_ok=True)
    p = PARTS / f"part-{time.time_ns()}.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema([(c, pa.float64() if c == "amount" else pa.string()) for c in COLS])), p, compression="zstd")
    return p


def merge():
    """Old table + new parts -> one table, newest version of each (contract, supplier); sorted by supplier code."""
    import duckdb
    parts = sorted(PARTS.glob("*.parquet")) if PARTS.exists() else []
    if not parts:
        return
    srcs = [f"'{p}'" for p in parts] + ([f"'{OUT}'"] if OUT.exists() else [])
    tmp = OUT.with_suffix(".tmp")
    duckdb.connect().execute(f"""COPY (SELECT {', '.join(COLS)} FROM read_parquet([{', '.join(srcs)}], union_by_name=true)
        QUALIFY row_number() OVER (PARTITION BY contract_id, supplier_code ORDER BY date_modified DESC) = 1
        ORDER BY supplier_code, date_signed) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)""")
    tmp.replace(OUT)
    for p in parts:
        p.unlink()


def walk(state, key, descending, deadline, ex, stats):
    """Follow one feed cursor until it is exhausted or the deadline passes; checkpoint parts + state as it goes."""
    buf = []
    while time.time() < deadline:
        q = f"{API}/contracts?limit={PAGE}&offset={state[key]}" + ("&descending=1" if descending else "")
        page = get(q) or {}
        ids = [x["id"] for x in page.get("data") or []]
        nxt = (page.get("next_page") or {}).get("offset")
        if not ids:
            if descending:
                state[key] = None  # history exhausted
            elif nxt:
                state[key] = nxt
            break
        for rows in ex.map(rows_for, ids):
            buf.extend(rows)
        stats[key] += len(ids)
        state[key] = nxt
        if len(buf) >= CHECKPOINT:
            write_part(buf)
            buf.clear()
            STATE.write_text(json.dumps(state))
            print(f"[prozorro] {key}: {stats[key]:,} contracts", flush=True)
    if buf:
        write_part(buf)
    STATE.write_text(json.dumps(state))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=float(os.environ.get("PROZORRO_MINUTES") or 60))
    ap.add_argument("--workers", type=int, default=int(os.environ.get("PROZORRO_WORKERS") or 8))
    a = ap.parse_args()
    LAKE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    deadline = t0 + a.minutes * 60
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not state:  # first run: forward from now, backward from now into history
        now = f"{time.time():.3f}"
        state = {"fwd": now, "back": now, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    stats = {"fwd": 0, "back": 0}
    with ThreadPoolExecutor(a.workers) as ex:
        walk(state, "fwd", False, deadline, ex, stats)
        if state.get("back"):
            walk(state, "back", True, deadline, ex, stats)
    merge()
    n = 0
    if OUT.exists():
        import duckdb
        n = duckdb.connect().execute(f"SELECT count(*) FROM '{OUT}'").fetchone()[0]
    print(f"[prozorro] +{stats['fwd']:,} new/changed, +{stats['back']:,} history; table {n:,} rows; "
          f"history {'done' if not state.get('back') else 'continues'}; {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
