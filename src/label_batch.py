"""Bulk labeling through the Anthropic Message Batches API (50% off, async, up to 24 h). Same prompt, schema,
validation and write path as label_messages.py, so batch labels are interchangeable with the interactive ones.

  python src/label_batch.py submit [--panel] [--newest] [--limit 30000]   # build requests, submit, save batch id to data/batches/
  python src/label_batch.py submit --topics-since 2026-07-01 --limit 5000  # re-label topic posts labeled before prompt v3 (mood)
  python src/label_batch.py fetch                            # fetch every submitted-but-unwritten batch, write labels
  python src/label_batch.py status

System prompt is sent with cache_control so the ~2k-token instructions are billed once per cache window.
"""
import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import PANEL_SQL, PROMPT_VERSION, SKIP_SOURCES, SKIP_TYPES, anthropic_text, clean, parse_json, system_prompt, claude  # noqa: E402
from topics import TOPIC_RX  # noqa: E402

BATCH_DIR = ROOT / "data" / "batches"
MODEL = "claude-haiku-5-5"

RESULTS_TTL_DAYS = 29  # the Batches API keeps results for 29 days after creation


def clients():
    """Every configured key: the current one first, then ANTHROPIC_API_KEY_LEGACY (comma-separated) for batches that
    were submitted under an older key/workspace. A batch is only visible to keys of the workspace that created it."""
    import os
    import anthropic
    keys = [os.environ.get("ANTHROPIC_API_KEY", "")] + os.environ.get("ANTHROPIC_API_KEY_LEGACY", "").split(",")
    return [claude(api_key=k.strip()) for k in dict.fromkeys(keys) if k.strip()]


WHY = {}  # batch_id -> why each key failed (printed by status)


def locate(cs, batch_id):
    """(client, batch) for the first key that can see the batch, or (None, None)."""
    import anthropic
    WHY[batch_id] = []
    for i, c in enumerate(cs):
        try:
            return c, c.messages.batches.retrieve(batch_id)
        except (anthropic.NotFoundError, anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError) as e:
            WHY[batch_id].append(f"key{i}: {type(e).__name__} {str(e)[:160]}")
    return None, None


def expired(meta):
    from datetime import datetime, timedelta, timezone
    sub = datetime.strptime(meta["submitted_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > sub + timedelta(days=RESULTS_TTL_DAYS)


def pending_ids():
    """Message ids already sent in a batch whose results are not written yet: still labeled_at IS NULL, must not be paid twice."""
    ids = set()
    for p in BATCH_DIR.glob("*.json"):
        meta = json.loads(p.read_text())
        if not meta.get("written"):
            ids.update(v["id"] for v in meta["ids"].values())
    return list(ids)


# Ukrainian sources only when the rules see a threat, target or timeframe. Measured on 19,963 unfiltered batch labels
# (2026-10-22): keeps 80% of posts and 96.8% of threat_level>=2 (510/527), 96.3% of >=1, 93.6% of rhetoric; the misses
# are mostly UA media retelling threats already labeled at their Russian source. Plain rule filtering kept only 82% of >=2.
# Topic posts (ТЦК, laws, corruption, prisoners, exchanges, ceasefire) always pass: they feed the mood view (~13/day, 30 days to 2026-10-22).
UA_FILTER = ("AND (s.country <> 'UA' OR EXISTS (SELECT 1 FROM rule_labels r WHERE r.message_id = m.id "
             "AND (r.r_threat OR r.r_target OR r.r_time)) OR m.text ~* '" + TOPIC_RX.replace("'", "''") + "') ")


def candidates(cur, a):
    cur.execute(
        "SELECT m.id, m.text, m.ts_published, s.id, s.country, s.type, s.lang FROM messages m JOIN sources s ON s.id=m.source_id "
        + ("WHERE m.labeled_at IS NOT NULL AND coalesce(m.prompt_version, '') NOT LIKE '%s%%%%' AND m.ts_published >= '%s' AND m.text ~* '%s' "
           % (PROMPT_VERSION, date.fromisoformat(a.topics_since), TOPIC_RX.replace("'", "''")) if a.topics_since else "WHERE m.labeled_at IS NULL ")
        + ("AND s.id = ANY(%s) AND m.ts_published >= '2026-03-31' AND m.id IN (SELECT message_id FROM rule_features WHERE r_threat) " % PANEL_SQL if a.panel else "")
        + ("" if a.all_ua or a.topics_since else UA_FILTER)
        + "AND length(m.text) >= 40 AND s.id <> ALL(%s) AND s.type <> ALL(%s) AND m.id <> ALL(%s) "
        "AND NOT (length(m.text) < 300 AND m.text ~* '(повітряна тривога|відбій повітряної|воздушная тревога|отбой воздушной)') "
        "ORDER BY m.ts_published " + ("DESC " if a.newest else "") + "LIMIT %s", (list(SKIP_SOURCES), list(SKIP_TYPES), pending_ids(), a.limit))
    return cur.fetchall()


def submit(a):
    import anthropic
    from load_to_db import connect
    client = claude()
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
            cid = re.sub(r"[^a-zA-Z0-9_-]", "_", mid)[:64]  # custom_id must match ^[a-zA-Z0-9_-]{1,64}$
            reqs.append({"custom_id": cid, "params": {"model": MODEL, "max_tokens": 4000, "output_config": {"effort": "low"}, "system": sysmsg,
                                                     "messages": [{"role": "user", "content": user}]}})
            pending[cid] = {"id": mid, "ts": ts.isoformat()}
        b = client.messages.batches.create(requests=reqs)
        meta = {"batch_id": b.id, "n": len(reqs), "model": MODEL, "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "written": False, "ids": {k: pending[k] for k in (r["custom_id"] for r in reqs)}}
        (BATCH_DIR / f"{b.id}.json").write_text(json.dumps(meta, ensure_ascii=False))
        print(f"submitted {b.id}: {len(reqs)} requests")


def write_bulk(cur, rows, model):
    """rows: [(message_id, ts, raw)] -> narratives, messages, message_labels in a handful of set-based statements
    (row-by-row round trips to Neon managed ~100 rows/min, too slow for 20k-request batches)."""
    pv = f"{PROMPT_VERSION}-anthropic-batch"
    span = {}
    for _, ts, raw in rows:
        lab = (raw.get("narrative_label") or "").strip()[:120]
        if lab:
            a0, b0 = span.get(lab, (ts, ts))
            span[lab] = (min(a0, ts), max(b0, ts))
    nid = {}
    if span:
        labs = list(span)
        first, last = [span[l][0] for l in labs], [span[l][1] for l in labs]
        cur.execute("INSERT INTO narratives(label, first_seen_ts, last_seen_ts) SELECT l, a, b FROM unnest(%s::text[], %s::timestamptz[], %s::timestamptz[]) x(l, a, b) "
                    "WHERE NOT EXISTS (SELECT 1 FROM narratives n WHERE n.label = x.l)", (labs, first, last))
        cur.execute("UPDATE narratives n SET last_seen_ts = GREATEST(n.last_seen_ts, x.b) FROM unnest(%s::text[], %s::timestamptz[]) x(l, b) WHERE n.label = x.l",
                    (labs, last))
        cur.execute("SELECT label, min(id) FROM narratives WHERE label = ANY(%s) GROUP BY 1", (labs,))
        nid = dict(cur.fetchall())
    cur.execute("CREATE TEMP TABLE IF NOT EXISTS lb (id text, event_kind text, threat_level int, novelty real, target_mentioned bool, timeframe_mentioned bool, "
                "is_scheduled_context bool, tone_amplification bool, llm_confidence real, nid bigint, raw jsonb) ON COMMIT DELETE ROWS")
    with cur.copy("COPY lb FROM STDIN") as cp:
        for mid, _, raw in rows:
            f = clean(raw)
            cp.write_row((mid, f["event_kind"], f["threat_level"], f["novelty"], f["target_mentioned"], f["timeframe_mentioned"],
                          f["is_scheduled_context"], f["tone_amplification"], f["llm_confidence"],
                          nid.get((raw.get("narrative_label") or "").strip()[:120]), json.dumps(raw, ensure_ascii=False)))
    cur.execute("UPDATE messages m SET event_kind=lb.event_kind, threat_level=lb.threat_level, novelty=lb.novelty, target_mentioned=lb.target_mentioned, "
                "timeframe_mentioned=lb.timeframe_mentioned, is_scheduled_context=lb.is_scheduled_context, tone_amplification=lb.tone_amplification, "
                "llm_confidence=lb.llm_confidence, narrative_id=lb.nid, prompt_version=%s, labeled_at=now() FROM lb WHERE m.id = lb.id", (pv,))
    cur.execute("INSERT INTO message_labels(message_id, prompt_version, model, raw, topics) SELECT id, %s, %s, raw, "
                "CASE WHEN jsonb_typeof(raw->'topics') = 'array' THEN raw->'topics' END FROM lb "
                "ON CONFLICT (message_id) DO UPDATE SET raw=EXCLUDED.raw, topics=EXCLUDED.topics, model=EXCLUDED.model, "
                "prompt_version=EXCLUDED.prompt_version, labeled_at=now(), archived_at=NULL",
                (pv, model))


def fetch(a):
    from datetime import datetime
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
            # created under a key/workspace we don't have (e.g. the revoked company key). Keep retrying while the
            # results still exist, so adding that workspace's key as ANTHROPIC_API_KEY_LEGACY recovers them; after
            # expiry mark it lost (its messages stay unlabeled and the next `submit` re-covers them).
            if expired(meta):
                meta["written"], meta["lost"] = True, f"results expired ({RESULTS_TTL_DAYS} days) and no configured key can see the batch"
                p.write_text(json.dumps(meta, ensure_ascii=False))
                print(meta["batch_id"], "expired; marked lost")
            else:
                print(meta["batch_id"], "not visible to any configured key; set ANTHROPIC_API_KEY_LEGACY to the key of the workspace that submitted it")
            continue
        print(meta["batch_id"], b.processing_status, dict(b.request_counts))
        if b.processing_status != "ended":
            continue
        ok = bad = 0
        use = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        rows = []
        for res in client.messages.batches.results(meta["batch_id"]):
            info = meta["ids"].get(res.custom_id)
            if not info:
                continue
            if res.result.type != "succeeded":
                bad += 1
                continue
            u = getattr(res.result.message, "usage", None)
            for k in use:
                use[k] += getattr(u, k, 0) or 0
            try:
                raw = parse_json(anthropic_text(res.result.message))
                clean(raw)  # validate now so one bad answer is skipped, not the whole bulk write
                rows.append((info["id"], datetime.fromisoformat(info["ts"]), raw))
            except Exception as e:  # noqa: BLE001
                bad += 1
                if bad <= 5:
                    print(f"skip {info['id']}: {e}")
        for i in range(0, len(rows), 5000):
            write_bulk(cur, rows[i:i + 5000], meta["model"])
            conn.commit()
            print(f"  written {min(i + 5000, len(rows))}/{len(rows)}", flush=True)
        ok = len(rows)
        meta["written"] = True
        meta["ok"], meta["bad"], meta["usage"] = ok, bad, use
        p.write_text(json.dumps(meta, ensure_ascii=False))
        # Haiku 5.5 batch prices: $0.05/M input, $0.25/M output, cache read $0.005/M, cache write $0.0625/M
        cost = use["input_tokens"] * .05 + use["output_tokens"] * .25 + use["cache_read_input_tokens"] * .005 + use["cache_creation_input_tokens"] * .0625
        print(f"written {ok}, failed {bad}; usage {use}; est. cost ${cost / 1e6:.2f}")


def status(a):
    cs = clients()
    for i, c in enumerate(cs):  # which workspace each key reaches: its most recent batches
        try:
            print(f"key{i}: recent batches", [b.id for b in c.messages.batches.list(limit=5)])
        except Exception as e:  # noqa: BLE001
            print(f"key{i}: {type(e).__name__} {str(e)[:200]}")
    for p in sorted(BATCH_DIR.glob("*.json")):
        meta = json.loads(p.read_text())
        if meta.get("lost"):
            print(meta["batch_id"], meta["n"], "lost:", meta["lost"])
            continue
        _, b = locate(cs, meta["batch_id"])
        if b is None:
            print(meta["batch_id"], meta["n"], "not visible to any configured key", "(expired)" if expired(meta) else "", WHY.get(meta["batch_id"]))
            continue
        print(meta["batch_id"], meta["n"], b.processing_status, dict(b.request_counts), "written" if meta.get("written") else "")

def drop(a):
    """Mark batches as lost now (e.g. their workspace key was revoked), so their posts can be submitted again."""
    for p in sorted(BATCH_DIR.glob("*.json")):
        meta = json.loads(p.read_text())
        if meta["batch_id"] in a.ids and not meta.get("written"):
            meta["written"], meta["lost"] = True, "dropped by hand: results unreachable"
            p.write_text(json.dumps(meta, ensure_ascii=False))
            print(meta["batch_id"], meta["n"], "dropped")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["submit", "fetch", "status", "drop"])
    ap.add_argument("ids", nargs="*", help="drop: batch ids")
    ap.add_argument("--panel", action="store_true")
    ap.add_argument("--limit", type=int, default=30000)
    ap.add_argument("--all-ua", action="store_true", help="submit: label every Ukrainian post too (default: UA only with rule signals)")
    ap.add_argument("--newest", action="store_true", help="submit: newest unlabeled first (default: oldest first)")
    ap.add_argument("--topics-since", default=None, help="submit: re-label posts with topic words (ТЦК, laws, corruption, prisoners, "
                    "exchanges, ceasefire) labeled before the current prompt, published since this date")
    a = ap.parse_args()
    {"submit": submit, "fetch": fetch, "status": status, "drop": drop}[a.cmd](a)
