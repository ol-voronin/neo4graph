"""LLM labeling of rhetoric features (docs/labeling_prompt.md). Needs DATABASE_URL and ANTHROPIC_API_KEY.

Picks unlabeled messages (labeled_at IS NULL), skips operational Kharkiv feeds and very short texts (those use
docs/labeling_modes.md Mode B), asks the model for strict JSON and writes the fields into `messages` + raw JSON
into `message_labels`. The model extracts features only; it never predicts strikes or judges truth.

Providers: --provider anthropic (ANTHROPIC_API_KEY, paid) or --provider gemini (GEMINI_API_KEY, free tier of Google AI
Studio; free-tier content may be used by Google to improve products, fine for public posts). The prompt, schema and
validation are identical, so labels from both can be compared on the same sample (prompt_version carries the provider).

Usage: python src/label_messages.py [--provider gemini] [--limit 200] [--model ...] [--dry-run] [--sample 20]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "v2"
SKIP_SOURCES = ("tg:tlknewsua", "tg:monitor1654", "tg:kpszsu")  # source ids carry the "tg:" prefix
SKIP_TYPES = ("osint",)  # alert/monitoring feeds: operational data, handled by rules / Mode B, not by the rhetoric prompt
KINDS = {"rhetoric", "operational", "anticorruption", "decision", "other"}


def system_prompt() -> str:
    md = (ROOT / "docs" / "labeling_prompt.md").read_text(encoding="utf-8")
    m = re.search(r"## System prompt\s+```\n(.*?)\n```", md, re.S)
    schema = re.search(r"## Выходная схема\s+```json\n(.*?)\n```", md, re.S)
    case = re.search(r"Блок `case`.*?```json\n(.*?)\n```", md, re.S)
    return f"{m.group(1)}\n\nСхема вывода:\n{schema.group(1)}\n\nБлок case:\n{case.group(1)}"


def parse_json(s: str):
    s = s.strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    return json.loads(s)


class QuotaExceeded(RuntimeError):
    """Gemini free-tier quota hit (per minute or per day). Retrying within one run only burns time: stop cleanly."""


def gemini_call(model: str, system: str, user: str, key: str) -> str:
    """Plain REST call (no SDK). Retries on 429/5xx with backoff; the free tier is rate limited."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({"systemInstruction": {"parts": [{"text": system}]},
                       "contents": [{"role": "user", "parts": [{"text": user}]}],
                       "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "maxOutputTokens": 800}}).encode()
    for attempt in range(5):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=60).read())
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code in (500, 503) and attempt < 4:
                time.sleep(15 * (attempt + 1))
                continue
            body = e.read()[:1200].decode("utf-8", "replace")
            if e.code == 429:
                raise QuotaExceeded(f"gemini HTTP 429: {body}") from e
            raise RuntimeError(f"gemini HTTP {e.code}: {body}") from e
    raise RuntimeError("gemini: retries exhausted")


def clean(o: dict):
    """Validate/clamp so a bad model answer cannot violate table constraints."""
    kind = o.get("event_kind")
    tl = o.get("threat_level")
    if kind not in KINDS or tl not in (0, 1, 2, 3):
        raise ValueError(f"bad kind/threat: {kind}/{tl}")

    def fl(k):
        v = o.get(k)
        return None if v is None else max(0.0, min(1.0, float(v)))

    if kind == "operational":
        tl = 0  # prompt rule 7 / example 4: operational = facts about actions, not rhetorical threat (models drift here)
    return dict(event_kind=kind, threat_level=int(tl), novelty=fl("novelty"),
                target_mentioned=bool(o.get("target_mentioned")), timeframe_mentioned=bool(o.get("timeframe_mentioned")),
                is_scheduled_context=bool(o.get("is_scheduled_context")), tone_amplification=bool(o.get("tone_amplification")),
                llm_confidence=fl("llm_confidence"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--provider", choices=["anthropic", "gemini"], default="anthropic")
    ap.add_argument("--model", default=None, help="default: claude-haiku-5-5 / gemini-3.5-flash-lite")
    ap.add_argument("--delay", type=float, default=None, help="seconds between calls (gemini default 6 = 10 req/min)")
    ap.add_argument("--dry-run", action="store_true", help="print results, do not write")
    ap.add_argument("--gold", action="store_true", help="only unlabeled posts that are in gold_sample (for LLM-vs-gold comparison)")
    ap.add_argument("--sample", type=int, default=0, help="random sample instead of oldest-first (for QA)")
    a = ap.parse_args()
    a.model = a.model or ("claude-haiku-5-5" if a.provider == "anthropic" else "gemini-3.5-flash-lite")
    delay = a.delay if a.delay is not None else (6.0 if a.provider == "gemini" else 0.0)
    keyname = "ANTHROPIC_API_KEY" if a.provider == "anthropic" else "GEMINI_API_KEY"
    if not os.environ.get(keyname):
        sys.exit(f"{keyname} is not set")
    sys.path.insert(0, str(ROOT / "src"))
    from load_to_db import connect
    client = None
    if a.provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
    pv = f"{PROMPT_VERSION}-{a.provider}"
    sysmsg = system_prompt()
    order = "random()" if a.sample else "m.ts_published DESC"
    conn = connect()
    cur = conn.cursor()
    if True:
        cur.execute(
            "SELECT m.id, m.text, m.ts_published, s.id, s.country, s.type, s.lang FROM messages m JOIN sources s ON s.id=m.source_id "
            "WHERE m.labeled_at IS NULL " + ("AND m.id IN (SELECT message_id FROM gold_sample) " if a.gold else "") + "AND length(m.text) >= 40 AND s.id <> ALL(%s) AND s.type <> ALL(%s) "
            "AND NOT (length(m.text) < 300 AND m.text ~* '(повітряна тривога|відбій повітряної|воздушная тревога|отбой воздушной)') ORDER BY " + order + " LIMIT %s",
            (list(SKIP_SOURCES), list(SKIP_TYPES), a.sample or a.limit))
        rows = cur.fetchall()
        cur.execute("SELECT label FROM narratives ORDER BY last_seen_ts DESC NULLS LAST LIMIT 20")
        recent = [r[0] for r in cur.fetchall()]
        ok = bad = quota_hits = 0
        quota_stop = False
        for i, (mid, text, ts, sid, country, stype, lang) in enumerate(rows):
            try:
                cur.execute("SELECT 1")
            except Exception:  # noqa: BLE001  (Neon drops idle SSL connections; reconnect and carry on)
                conn = connect()
                cur = conn.cursor()
            if i and i % 10 == 0:  # refresh so novelty/narrative reuse sees labels from this very run
                cur.execute("SELECT label FROM narratives ORDER BY last_seen_ts DESC NULLS LAST LIMIT 20")
                recent = [r[0] for r in cur.fetchall()]
            user = json.dumps({"source": {"name": sid, "country": country, "type": stype, "lang": lang},
                               "ts_published": ts.isoformat(), "text": text[:3000], "recent_narratives": recent}, ensure_ascii=False)
            try:
                if a.provider == "anthropic":
                    resp = client.messages.create(model=a.model, max_tokens=800, system=sysmsg,
                                                  messages=[{"role": "user", "content": user}])
                    txt = resp.content[0].text
                else:
                    txt = gemini_call(a.model, sysmsg, user, os.environ["GEMINI_API_KEY"])
                    time.sleep(delay)
                raw = parse_json(txt)
                f = clean(raw)
            except QuotaExceeded as e:
                quota_hits += 1
                print(f"quota: {str(e)[:700]}")
                if quota_hits >= 2:
                    print("Gemini quota exhausted (free tier); stopping this run cleanly, the next scheduled run continues.")
                    quota_stop = True
                    break
                time.sleep(65)  # first hit may be the per-minute limit
                continue
            except Exception as e:  # noqa: BLE001
                bad += 1
                print(f"skip {mid}: {e}")
                continue
            quota_hits = 0
            ok += 1
            if a.dry_run:
                print(mid, f["event_kind"], f["threat_level"], (raw.get("narrative_label") or ""), "|", text[:90].replace("\n", " "))
                continue
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
            cur.execute(
                "UPDATE messages SET event_kind=%(event_kind)s, threat_level=%(threat_level)s, novelty=%(novelty)s, "
                "target_mentioned=%(target_mentioned)s, timeframe_mentioned=%(timeframe_mentioned)s, "
                "is_scheduled_context=%(is_scheduled_context)s, tone_amplification=%(tone_amplification)s, "
                "llm_confidence=%(llm_confidence)s, narrative_id=%(nid)s, prompt_version=%(pv)s, labeled_at=now() WHERE id=%(id)s",
                {**f, "nid": nid, "pv": pv, "id": mid})
            cur.execute("INSERT INTO message_labels(message_id, prompt_version, model, raw) VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT (message_id) DO UPDATE SET raw=EXCLUDED.raw, model=EXCLUDED.model, prompt_version=EXCLUDED.prompt_version, labeled_at=now()",
                        (mid, pv, a.model, json.dumps(raw, ensure_ascii=False)))
            conn.commit()
    print(f"labeled {ok}, failed {bad}" + (", stopped on quota" if quota_stop else ""))
    if bad and not ok:
        sys.exit(1)  # make a total failure visible (red run) instead of a silent green one


if __name__ == "__main__":
    main()
