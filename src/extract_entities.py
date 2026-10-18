"""Entity extraction for the anti-corruption layer (docs/entity_extraction.md). Gemini free tier, same REST helper as
label_messages.py. Writes one row per message into entity_extractions; a post-processing filter strips PII patterns the
model may have let through (phones, e-mails, tax ids, card numbers), so the data rules do not depend on the prompt alone.

Usage: python src/extract_entities.py [--limit 50] [--sources tg:sap_gov_ua,...] [--dry-run] [--model gemini-3.5-flash-lite]
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import QuotaExceeded, gemini_call, parse_json  # noqa: E402

PROMPT_VERSION = "ee-v1"
ANTICORR_SOURCES = ["tg:sap_gov_ua", "tg:NAZK_gov_ua", "tg:PressCourt", "tg:fuckcorruption", "tg:fightcorruptor",
                    "tg:BihusInfo", "tg:slidstvo_info"]
ROLES = {"suspect", "defendant", "convicted", "acquitted", "official_commenting", "investigator", "prosecutor", "judge",
         "lawyer", "journalist", "politician_mentioned", "owner", "other", "private_skipped"}

# Post-filter: anything matching these is removed from string fields regardless of what the model did.
PII_PATTERNS = [
    re.compile(r"\+?\d[\d\s\-()]{8,}\d"),                  # phone-like digit runs
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                # e-mail
    re.compile(r"\b\d{10}\b"),                             # RNOKPP / tax id (10 digits)
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                 # card / IBAN-like digit runs
    re.compile(r"\b[A-ZА-ЯІЇЄ]{2}\d{6}\b"),                # passport-like
]


def system_prompt() -> str:
    md = (ROOT / "docs" / "entity_extraction.md").read_text(encoding="utf-8")
    sp = re.search(r"## System prompt\s+```\n(.*?)\n```", md, re.S).group(1)
    schema = re.search(r"## Output schema \(strict JSON\)\s+```json\n(.*?)\n```", md, re.S).group(1)
    return f"{sp}\n\nСхема виводу:\n{schema}"


def scrub(v):
    """Recursively remove PII-looking substrings from all strings in the payload."""
    if isinstance(v, str):
        out = v
        for p in PII_PATTERNS:
            out = p.sub("[removed]", out)
        return out
    if isinstance(v, list):
        return [scrub(x) for x in v]
    if isinstance(v, dict):
        return {k: scrub(x) for k, x in v.items()}
    return v


def clean(o: dict) -> dict:
    if not isinstance(o, dict) or "is_case_related" not in o:
        raise ValueError("no is_case_related")
    out = {"is_case_related": bool(o.get("is_case_related")), "pii_dropped": bool(o.get("pii_dropped"))}
    for k in ("cases", "persons", "orgs", "assets", "amounts", "claims"):
        v = o.get(k) or []
        if not isinstance(v, list):
            raise ValueError(f"{k} not a list")
        out[k] = [x for x in v if isinstance(x, dict)]
    persons = []
    for p in out["persons"]:
        role = p.get("role") if p.get("role") in ROLES else "other"
        if role == "private_skipped" or not p.get("name_norm"):
            persons.append({"role": "private_skipped"})  # never carry a name for private persons
            continue
        p["role"] = role
        p["public_figure"] = True
        persons.append(p)
    out["persons"] = persons
    # plates only for assets explicitly linked to a public person (name_norm present); otherwise drop
    for a in out["assets"]:
        if a.get("plate_or_vin") and not a.get("linked_person"):
            a["plate_or_vin"] = None
            out["pii_dropped"] = True
    scrubbed = scrub(out)
    if json.dumps(scrubbed, ensure_ascii=False) != json.dumps(out, ensure_ascii=False):
        scrubbed["pii_dropped"] = True
    return scrubbed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--sources", default=",".join(ANTICORR_SOURCES))
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--delay", type=float, default=6.0)
    ap.add_argument("--min-len", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--random", action="store_true", help="random order instead of newest-first")
    a = ap.parse_args()
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY is not set")
    from load_to_db import connect
    sysmsg = system_prompt()
    srcs = [s.strip() for s in a.sources.split(",") if s.strip()]
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT m.id, m.text, m.ts_published, m.source_id FROM messages m "
                "LEFT JOIN entity_extractions e ON e.message_id = m.id "
                "WHERE e.message_id IS NULL AND m.source_id = ANY(%s) AND length(m.text) >= %s "
                "ORDER BY " + ("random()" if a.random else "m.ts_published DESC") + " LIMIT %s", (srcs, a.min_len, a.limit))
    rows = cur.fetchall()
    print(f"candidates: {len(rows)}")
    ok = bad = quota_hits = 0
    quota_stop = False
    for mid, text, ts, sid in rows:
        try:
            cur.execute("SELECT 1")
        except Exception:  # noqa: BLE001
            conn = connect()
            cur = conn.cursor()
        user = json.dumps({"source": sid, "ts_published": ts.isoformat(), "text": text[:4000]}, ensure_ascii=False)
        try:
            txt = gemini_call(a.model, sysmsg, user, os.environ["GEMINI_API_KEY"], max_tokens=4000)
            time.sleep(a.delay)
            payload = clean(parse_json(txt))
        except QuotaExceeded as e:
            quota_hits += 1
            print(f"quota: {str(e)[:500]}")
            if quota_hits >= 2:
                quota_stop = True
                break
            time.sleep(65)
            continue
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"skip {mid}: {e}")
            continue
        quota_hits = 0
        ok += 1
        if a.dry_run:
            print(json.dumps({"id": mid, "src": sid, **payload}, ensure_ascii=False)[:1500])
            continue
        cur.execute("INSERT INTO entity_extractions(message_id, is_case_related, payload, pii_dropped, model, prompt_version) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (message_id) DO UPDATE SET payload=EXCLUDED.payload, "
                    "is_case_related=EXCLUDED.is_case_related, pii_dropped=EXCLUDED.pii_dropped, model=EXCLUDED.model, "
                    "prompt_version=EXCLUDED.prompt_version, created_at=now()",
                    (mid, payload["is_case_related"], json.dumps(payload, ensure_ascii=False), payload["pii_dropped"],
                     a.model, PROMPT_VERSION))
        conn.commit()
    print(f"extracted {ok}, failed {bad}" + (", stopped on quota" if quota_stop else ""))
    if bad and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
