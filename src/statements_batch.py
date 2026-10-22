"""Statements layer through the Anthropic Message Batches API (50% off, async, <24 h): same prompts, candidates, evidence
and write paths as extract_statements.py / resolve_statements.py, so batch rows are interchangeable with interactive ones.

  python src/statements_batch.py submit-extract [--limit 800]              # key actors' channels first, then media
  python src/statements_batch.py submit-resolve [--limit 400]              # due forecasts + facts + retry of unresolvable
  python src/statements_batch.py fetch                                     # write every finished batch
  python src/statements_batch.py status

The daily cap is --limit (the workflow submits once a day). Each submit prints an estimate before sending; --estimate
prints it and exits. Batch metadata lives in data/batches_st/ (kept apart from label_batch.py's data/batches/).
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import anthropic_text, parse_json, claude  # noqa: E402
from label_batch import clients, locate, expired, WHY  # noqa: E402
import extract_statements as X  # noqa: E402
import resolve_statements as R  # noqa: E402

BATCH_DIR = ROOT / "data" / "batches_st"
MODEL = "claude-haiku-5-5"
# Haiku 5.5 batch prices per 1M tokens, as in label_batch.py: input, output, cache read, cache write
PRICE = {"input_tokens": .05, "output_tokens": .25, "cache_read_input_tokens": .005, "cache_creation_input_tokens": .0625}
OUT_EST = {"extract": 450, "resolve": 220}  # average output tokens per request (low effort, JSON only)


def tokens(s: str) -> int:
    return len(s) // 3 + 1  # Cyrillic-heavy text: ~3 chars per token


def pending(kind):
    """ids already sent in an unwritten batch of this kind (must not be paid twice)"""
    out = set()
    for p in BATCH_DIR.glob("*.json"):
        m = json.loads(p.read_text())
        if m.get("kind") == kind and not m.get("written"):
            out.update(v["id"] for v in m["ids"].values())
    return out


def cid(x) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(x))[:64]


def send(kind, reqs, ids, sys_tokens, a):
    n = len(reqs)
    inp = sum(tokens(r["params"]["messages"][0]["content"]) for r in reqs)
    est = (inp * PRICE["input_tokens"] + n * sys_tokens * PRICE["cache_read_input_tokens"] + sys_tokens * PRICE["cache_creation_input_tokens"]
           + n * OUT_EST[kind] * PRICE["output_tokens"]) / 1e6
    print(f"{kind}: {n} requests, ~{inp:,} input tokens + {sys_tokens:,} cached system; estimated ${est:.3f}")
    if a.estimate or not reqs:
        return
    b = claude().messages.batches.create(requests=reqs)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"batch_id": b.id, "kind": kind, "n": n, "model": MODEL, "est_usd": round(est, 4),
            "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "written": False, "ids": ids}
    (BATCH_DIR / f"{b.id}.json").write_text(json.dumps(meta, ensure_ascii=False))
    print(f"submitted {b.id}")


def submit_extract(a):
    from load_to_db import connect
    cur = connect().cursor()
    rows = X.candidates(cur, a.limit, a.since, exclude=pending("extract"))
    sysmsg = X.system_prompt()
    system = [{"type": "text", "text": sysmsg, "cache_control": {"type": "ephemeral"}}]
    reqs, ids = [], {}
    for mid, text, ts, sid, country, stype in rows:
        c = cid(mid)
        reqs.append({"custom_id": c, "params": {"model": MODEL, "max_tokens": 6000, "output_config": {"effort": "low"}, "system": system,
                                                "messages": [{"role": "user", "content": X.user_msg(text, ts, sid, country, stype)}]}})
        ids[c] = {"id": mid, "ts": ts.isoformat()}
    by = {}
    for r in rows:
        by[r[3]] = by.get(r[3], 0) + 1
    print("by source:", dict(sorted(by.items(), key=lambda kv: -kv[1])))
    send("extract", reqs, ids, tokens(sysmsg), a)


def submit_resolve(a):
    """Due forecasts first, then claims of fact, then retries of 'unresolvable', up to --limit in total."""
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    seen = {int(x) for x in pending("resolve")}
    system = [{"type": "text", "text": R.SYSTEM, "cache_control": {"type": "ephemeral"}}]
    reqs, ids, left = [], {}, a.limit
    for mode, share in (("due", .5), ("facts", .3), ("retry", 1.0)):
        take = max(0, min(left, int(a.limit * share)))
        if not take:
            continue
        rows = R.due(cur, mode, take, exclude=seen)
        for row in rows:
            ev = R.gather(cur, conn, row, mode)
            c = cid(f"s{row[0]}")
            reqs.append({"custom_id": c, "params": {"model": MODEL, "max_tokens": 3000, "output_config": {"effort": "low"}, "system": system,
                                                    "messages": [{"role": "user", "content": R.payload(row, ev)}]}})
            ids[c] = {"id": row[0], "mode": mode, "ev": [e[0] for e in ev]}
            seen.add(row[0])
        left -= len(rows)
        print(f"{mode}: {len(rows)}")
    send("resolve", reqs, ids, tokens(R.SYSTEM), a)


def fetch(a):
    from load_to_db import connect
    cs = clients()
    conn = connect()
    cur = conn.cursor()
    for p in sorted(BATCH_DIR.glob("*.json")):
        meta = json.loads(p.read_text())
        if meta.get("written"):
            continue
        client, b = locate(cs, meta["batch_id"])
        if b is None:
            if expired(meta):
                meta["written"], meta["lost"] = True, "results expired and no configured key can see the batch"
                p.write_text(json.dumps(meta, ensure_ascii=False))
            print(meta["batch_id"], "not visible to any configured key", WHY.get(meta["batch_id"]))
            continue
        print(meta["batch_id"], meta.get("kind"), b.processing_status, dict(b.request_counts))
        if b.processing_status != "ended":
            continue
        ok = bad = 0
        use = dict.fromkeys(PRICE, 0)
        verdicts = {}
        pv = f"{X.PROMPT_VERSION}-anthropic-batch"
        # Idempotent: a fetch that died half-way (the results stream broke on 2026-10-22 after part of a 29 k batch) is
        # re-run; posts already extracted / statements already resolved since submission are skipped, not written twice.
        ids = [v["id"] for v in meta["ids"].values()]
        if meta.get("kind") == "resolve":
            cur.execute("SELECT id FROM statements WHERE id = ANY(%s) AND resolved_at >= %s", ([int(x) for x in ids], meta["submitted_at"]))
        else:
            cur.execute("SELECT message_id FROM statement_extractions WHERE message_id = ANY(%s)", ([str(x) for x in ids],))
        done = {str(r[0]) for r in cur.fetchall()}
        seen, n = set(), 0
        for attempt in range(5):
            try:
                for res in client.messages.batches.results(meta["batch_id"]):
                    if res.custom_id in seen:
                        continue
                    seen.add(res.custom_id)
                    info = meta["ids"].get(res.custom_id)
                    if not info or res.result.type != "succeeded":
                        bad += 1
                        continue
                    if str(info["id"]) in done:
                        continue
                    u = getattr(res.result.message, "usage", None)
                    for k in use:
                        use[k] += getattr(u, k, 0) or 0
                    cur.execute("SAVEPOINT r")  # a bad item rolls back itself only, not the uncommitted good ones
                    try:
                        r = parse_json(anthropic_text(res.result.message))
                        if meta.get("kind") == "resolve":
                            v = R.apply(cur, info["id"], r, info["ev"])
                            verdicts[v] = verdicts.get(v, 0) + 1
                        else:
                            sts, evs = X.clean(r)
                            X.write(cur, info["id"], datetime.fromisoformat(info["ts"]), sts, evs, meta["model"], pv)
                        cur.execute("RELEASE SAVEPOINT r")
                        ok += 1
                    except Exception as e:  # noqa: BLE001
                        cur.execute("ROLLBACK TO SAVEPOINT r")
                        bad += 1
                        if bad <= 5:
                            print(f"skip {info['id']}: {e}")
                    n += 1
                    if n % 200 == 0:
                        conn.commit()
                break
            except Exception as e:  # noqa: BLE001  the results stream broke: keep what is written, read again, skip what is seen
                conn.commit()
                print(f"results stream broke after {len(seen)} ({type(e).__name__}: {str(e)[:120]}); retry {attempt + 1}")
                time.sleep(10 * (attempt + 1))
        else:
            conn.commit()
            print(meta["batch_id"], "results stream kept breaking; not marked written, the next fetch resumes")
            continue
        conn.commit()
        cost = sum(use[k] * PRICE[k] for k in PRICE) / 1e6
        meta.update(written=True, ok=ok, bad=bad, usage=use, usd=round(cost, 4), verdicts=verdicts)
        p.write_text(json.dumps(meta, ensure_ascii=False))
        print(f"written {ok}, failed {bad}; {verdicts or ''} usage {use}; cost ${cost:.3f} (estimated ${meta.get('est_usd')})")


def status(a):
    cs = clients()
    for p in sorted(BATCH_DIR.glob("*.json")):
        meta = json.loads(p.read_text())
        _, b = (None, None) if meta.get("lost") else locate(cs, meta["batch_id"])
        print(meta["batch_id"], meta.get("kind"), meta["n"], b.processing_status if b else meta.get("lost", "not visible"),
              "written" if meta.get("written") else "", f"${meta.get('usd', meta.get('est_usd'))}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["submit-extract", "submit-resolve", "fetch", "status"])
    ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--since", default="2026-03-31")
    ap.add_argument("--estimate", action="store_true", help="print the request count and cost estimate, do not submit")
    a = ap.parse_args()
    {"submit-extract": submit_extract, "submit-resolve": submit_resolve, "fetch": fetch, "status": status}[a.cmd](a)
