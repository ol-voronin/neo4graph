"""Public money received by a company: spending.gov.ua (Є-Data) transactions API, free, no key.
Companies are identified by ЄДРПОУ (from edr_orgs or given); ФОП are not covered (they would need a tax id, which we never use).
The API serves at most 92 days per call, so --quarters N walks back N quarters (default 8 = two years).

  python src/spending.py --edrpou 24253556 [--quarters 8]
  python src/spending.py --from-db            # every edr_orgs row matched by a query
Writes public_payments (one row per transaction) and prints a summary; the graph shows payer → company edges with totals.
"""
import argparse
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
API = "https://api.spending.gov.ua/api/v2/api/transactions/"
UA = {"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)", "accept": "application/json"}


def fetch(edrpou, start, end):
    url = API + "?" + urllib.parse.urlencode({"recipt_edrpous": edrpou, "startdate": start.isoformat(), "enddate": end.isoformat()})
    data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90).read()
    return json.loads(data) if data.strip().startswith(b"[") else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edrpou", default="", help="comma-separated ЄДРПОУ codes")
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--quarters", type=int, default=8)
    ap.add_argument("--query", default="", help="with --from-db: only companies matched by this query (edr_orgs.matched_queries)")
    ap.add_argument("--cap", type=int, default=1500, help="stop paging a company after this many transactions (state giants)")
    a = ap.parse_args()
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS public_payments (id bigint PRIMARY KEY, recipt_edrpou text NOT NULL, recipt_name text, payer_edrpou text, payer_name text, "
                "trans_date date, amount numeric, currency text, payment_details text, doc_number text, fetched_at timestamptz DEFAULT now())")
    cur.execute("CREATE INDEX IF NOT EXISTS public_payments_recipt_idx ON public_payments(recipt_edrpou)")
    codes = [c.strip() for c in a.edrpou.split(",") if c.strip()]
    if a.from_db:
        if a.query:
            cur.execute("SELECT edrpou FROM edr_orgs WHERE edrpou ~ '^\\d{8}$' AND EXISTS (SELECT 1 FROM unnest(matched_queries) m WHERE m ILIKE %s) ORDER BY updated_at DESC LIMIT 12", ("%" + a.query + "%",))
        else:
            cur.execute("SELECT edrpou FROM edr_orgs WHERE edrpou ~ '^\\d{8}$' ORDER BY updated_at DESC LIMIT 12")
        codes += [r[0] for r in cur.fetchall()]
    codes = list(dict.fromkeys(codes))
    if not codes:
        sys.exit("no ЄДРПОУ codes")
    today = dt.date.today()
    for code in codes:
        n = total = 0
        payers = {}
        end = today
        for _ in range(a.quarters):
            start = end - dt.timedelta(days=91)
            try:
                rows = fetch(code, start, end)
            except Exception as e:  # noqa: BLE001
                print(f"{code} {start}..{end}: {str(e)[:120]}")
                rows = []
            for r in rows:
                cur.execute("INSERT INTO public_payments(id, recipt_edrpou, recipt_name, payer_edrpou, payer_name, trans_date, amount, currency, payment_details, doc_number) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                            (r["id"], r.get("recipt_edrpou"), r.get("recipt_name"), r.get("payer_edrpou"), r.get("payer_name"), r.get("trans_date"),
                             r.get("amount"), r.get("currency"), (r.get("payment_details") or "")[:600], r.get("doc_number")))
                n += 1
                total += float(r.get("amount") or 0)
                payers[r.get("payer_name")] = payers.get(r.get("payer_name"), 0) + float(r.get("amount") or 0)
            conn.commit()
            end = start - dt.timedelta(days=1)
            time.sleep(0.7)
            if n >= a.cap:
                print(f"{code}: cap {a.cap} reached, stopping early")
                break
        top = sorted(payers.items(), key=lambda x: -x[1])[:3]
        print(f"{code}: {n} платежів з бюджету за {a.quarters} кв., {total:,.0f} грн; топ платники: " + "; ".join(f"{k} ({v:,.0f})" for k, v in top))


if __name__ == "__main__":
    main()
