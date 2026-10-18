"""Bulk labeling through the Anthropic Message Batches API (50% off, async, up to 24 h). Same prompt, schema,
validation and write path as label_messages.py, so batch labels are interchangeable with the interactive ones.

  python src/label_batch.py submit --panel [--limit 30000]   # build requests, submit, save batch id to data/batches/
  python src/label_batch.py fetch                            # fetch every submitted-but-unwritten batch, write labels
  python src/label_batch.py status

System prompt is sent with cache_control so the ~2k-token instructions are billed once per cache window.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import PANEL_SQL, PROMPT_VERSION, SKIP_SOURCES, SKIP_TYPES, anthropic_text, clean, parse_json, system_prompt  # noqa: E402

BATCH_DIR = ROOT / "data" / "batches"
MODEL = "claude-haiku-5-5"


def candidates(cur, a):
    cur.execute(
        "SELECT m.id, m.text, m.ts_published, s.id, s.country, s.type, s.lang FROM messages m JOIN sources s ON s.id=m.source_id "
        "WHERE m.labeled_at IS NULL "
        + ("AND s.id = ANY(%s) AND m.ts_published >= '2026-03-31' AND m.id IN (SELECT message_id FROM rule_features WHERE r_threat) " % PANEL_SQL if a.panel else "")
        + "AND length(m.text) >= 40 AND s.id <> ALL(%s) AND s.type <> ALL(%s) "
        "AND NOT (length(m.text) < 300 AND m.text ~* '(повітряна тривога|відбій повітряної|воздушная тревога|отбой воздушной)') "
        "ORDER BY m.ts_published LIMIT %s", (list(SKIP_SOURCES), list(SKIP_TYPES), a.limit))
    return cur.fetchall()


def submit(a):
    import anthropic
    from load_to_db import connect
    client = anthropic.Anthropic()
    conn = connect()
    cur = conn.cursor()
    rows = candidates(cur, a)
    print(f"candidates: {len(rows)}")
    if not rows:
        return
    cur.execute("SELECT label FROM narratives ORDER BY last_seen_ts DESC NULLS LAST LIMIT 20")
    recent = [r[0] for r in cur.fetchall()]
    sysmsg = [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}]
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    pending = {}
    for i in range(0, len(rows), 20000):  # keep each batch comfortably under API limits
        chunk = rows[i:i + 20000]
        reqs = []
        for mid, text, ts, sid, country, stype, lang in chunk:
            user = json.dumps({"source": {"name": sid, "country": country, "type": stype, "lang": lang},
                               "ts_published": ts.isoformat(), "text": text[:3000], "recent_narratives": recent}, ensure_ascii=False)
            reqs.append({"custom_id": mid[:64], "params": {"model": MODEL, "max_tokens": 4000, "output_config": {"effort": "low"}, "system": sysmsg,
                                                          "messages": [{"role": "user", "content": user}]}})
            pending[mid[:64]] = {"id": mid, "ts": ts.isoformat()}
        b = client.messages.batches.create(requests=reqs)
        meta = {"batch_id": b.id, "n": len(reqs), "model": MODEL, "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "written": False, "ids": {k: pending[k] for k in (r["custom_id"] for r in reqs)}}
        (BATCH_DIR / f"{b.id}.json").write_text(json.dumps(meta, ensure_ascii=False))
        print(f"submitted {b.id}: {len(reqs)} requests")


def write_result(cur, raw, mid, ts, model):
    f = clean(raw)
    label = (raw.get("narrative_label") or "").strip()[:120]
    nid = None
    if label:
        cur.execute("SELECT id FROM narratives WHERE label=%s", (label,))
        r = cur.fetchone()
        if r:
            nid = r[0]
            cur.execute("UPDATE narratives SET last_seen_ts=GREATEST(last_seen_ts,%s) WHERE id=%s", (ts, nid))
        else:
            cur.execute("INSERT INTO narratives(label, first_seen_ts, last_seen_ts) VALUES (%s,%s,%s) RETURNING id", (label, ts, ts))
            nid = cur.fetchone()[0]
    pv = f"{PROMPT_VERSION}-anthropic-batch"
    cur.execute(
        "UPDATE messages SET event_kind=%(event_kind)s, threat_level=%(threat_level)s, novelty=%(novelty)s, "
        "target_mentioned=%(target_mentioned)s, timeframe_mentioned=%(timeframe_mentioned)s, "
        "is_scheduled_context=%(is_scheduled_context)s, tone_amplification=%(tone_amplification)s, "
        "llm_confidence=%(llm_confidence)s, narrative_id=%(nid)s, prompt_version=%(pv)s, labeled_at=now() WHERE id=%(id)s",
        {**f, "nid": nid, "pv": pv, "id": mid})
    cur.execute("INSERT INTO message_labels(message_id, prompt_version, model, raw) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (message_id) DO UPDATE SET raw=EXCLUDED.raw, model=EXCLUDED.model, prompt_version=EXCLUDED.prompt_version, labeled_at=now()",
                (mid, pv, model, json.dumps(raw, ensure_ascii=False)))


def fetch(a):
    import anthropic
    from datetime import datetime
    from load_to_db import connect
    client = anthropic.Anthropic()
    conn = connect()
    cur = conn.cursor()
    for p in sorted(BATCH_DIR.glob("*.json")):
        meta = json.loads(p.read_text())
        if meta.get("written"):
            continue
        b = client.messages.batches.retrieve(meta["batch_id"])
        print(meta["batch_id"], b.processing_status, dict(b.request_counts))
        if b.processing_status != "ended":
            continue
        ok = bad = 0
        for i, res in enumerate(client.messages.batches.results(meta["batch_id"])):
            info = meta["ids"].get(res.custom_id)
            if not info:
                continue
            if res.result.type != "succeeded":
                bad += 1
                continue
            try:
                raw = parse_json(anthropic_text(res.result.message))
                write_result(cur, raw, info["id"], datetime.fromisoformat(info["ts"]), meta["model"])
                ok += 1
            except Exception as e:  # noqa: BLE001
                bad += 1
                if bad <= 5:
                    print(f"skip {info['id']}: {e}")
            if i % 500 == 0:
                conn.commit()
        conn.commit()
        meta["written"] = True
        meta["ok"], meta["bad"] = ok, bad
        p.write_text(json.dumps(meta, ensure_ascii=False))
        print(f"written {ok}, failed {bad}")


def status(a):
    import anthropic
    client = anthropic.Anthropic()
    for p in sorted(BATCH_DIR.glob("*.json")):
        meta = json.loads(p.read_text())
        b = client.messages.batches.retrieve(meta["batch_id"])
        print(meta["batch_id"], meta["n"], b.processing_status, dict(b.request_counts), "written" if meta.get("written") else "")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["submit", "fetch", "status"])
    ap.add_argument("--panel", action="store_true")
    ap.add_argument("--limit", type=int, default=30000)
    a = ap.parse_args()
    {"submit": submit, "fetch": fetch, "status": status}[a.cmd](a)
