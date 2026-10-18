"""Statements / actions / regional strike events extraction (docs/statements_layer.md). One LLM call per post over
official, regional and news sources; writes statements, events and a per-message marker. Gemini (free tier) or
Anthropic (ANTHROPIC_API_KEY, --provider anthropic).

Usage: python src/extract_statements.py [--limit 60] [--provider gemini|anthropic] [--random] [--dry-run] [--since 2026-03-31]
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import QuotaExceeded, anthropic_text, gemini_call, parse_json  # noqa: E402

PROMPT_VERSION = "st-v1"
SOURCES = ["tg:MID_Russia", "tg:news_kremlin", "tg:medvedev_telegram", "tg:mod_russia",            # RU official
           "tg:V_Zelenskiy_official", "tg:GeneralStaffZSU", "tg:kpszsu",                             # UA official
           "tg:synegubov", "tg:kharkivoda", "tg:kyivoda", "tg:mykolaiv_ova", "tg:vinnytsiaoda",       # regional
           "rss:whitehouse", "tg:golosameriki",                                                       # US
           "tg:ukrpravda_news", "tg:kyivindependent", "tg:bbcukrainian", "tg:bbcrussian", "tg:radiosvoboda",
           "tg:suspilnenews", "tg:censor_net", "tg:hromadske_ua", "tg:rian_ru", "tg:tass_agency"]     # media
# Media posts are candidates only if they look like statements/actions/strikes (saves quota).
MEDIA_RE = (r"(заяви|заявил|заявля|погроз|пригроз|угрож|обіця|пообещ|обещ|попередив|предупред|ультимат|"
            r"санкці|санкци|допомог|помощь|пакет|поставк|постач|Patriot|ATACMS|Tomahawk|F-16|"
            r"Трамп|Trump|Венс|Vance|Рубіо|Рубио|Rubio|Пентагон|Білий дім|Белый дом|Конгрес|Сенат|сенатор|республіканц|республиканц|демократ|"
            r"Путін|Путин|Лавров|Пєсков|Песков|Медвєдєв|Медведев|Кремл|"
            r"ракет|дрон|шахед|Shahed|КАБ|балістич|баллистич|удар|обстріл|обстрел|загинул|погиб|поранен|ранен)")
SIDES = {"RU_official", "RU_milblogger", "RU_media", "UA_official", "UA_media", "US_admin", "US_right", "US_left",
         "US_congress_bipartisan", "US_other", "EU", "NATO", "other"}
STYPES = {"threat", "promise", "forecast", "claim_fact", "demand", "denial", "announce_action"}
ETYPES = {"strike", "sanction", "aid", "weapons_delivery", "mobilisation", "negotiation", "attack_on_russia", "legal_act", "other"}
REGIONS = {"cherkaska", "chernihivska", "chernivetska", "dnipropetrovska", "donetska", "ivano-frankivska", "kharkivska",
           "khersonska", "khmelnytska", "kirovohradska", "kyiv-city", "kyivska", "luhanska", "lvivska", "mykolaivska",
           "odeska", "poltavska", "rivnenska", "sumska", "ternopilska", "vinnytska", "volynska", "zakarpatska",
           "zaporizka", "zhytomyrska", "ukraine", "russia"}


def system_prompt() -> str:
    md = (ROOT / "docs" / "statements_layer.md").read_text(encoding="utf-8")
    sp = re.search(r"## System prompt\s+```\n(.*?)\n```", md, re.S).group(1)
    schema = re.search(r"## Output schema \(strict JSON\)\s+```json\n(.*?)\n```", md, re.S).group(1)
    return f"{sp}\n\nПерелік областей (slug): {', '.join(sorted(REGIONS))}.\n\nСхема виводу:\n{schema}"


def _int(v):
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _region(v):
    if not v:
        return None
    v = str(v).strip().lower()
    if v in REGIONS or v.startswith("russia:"):
        return v
    return None


def clean(o: dict):
    sts, evs = [], []
    for s in (o.get("statements") or []):
        if not isinstance(s, dict) or not s.get("quote") or s.get("stype") not in STYPES:
            continue
        side = s.get("actor_side") if s.get("actor_side") in SIDES else "other"
        resolvable = bool(s.get("resolvable")) and bool(s.get("predicted_event"))
        sts.append(dict(actor_side=side, actor_name=(s.get("actor_name") or None), stype=s["stype"],
                        quote=str(s["quote"])[:300], predicted_event=(s.get("predicted_event") or None),
                        target_region=_region(s.get("target_region")),
                        horizon_days=min(365, max(1, _int(s.get("horizon_days")) or 30)), resolvable=resolvable))
    for e in (o.get("events") or []):
        if not isinstance(e, dict) or e.get("event_type") not in ETYPES:
            continue
        d = e.get("event_date")
        try:
            d = date.fromisoformat(str(d)[:10]) if d else None
        except ValueError:
            d = None
        try:
            conf = min(1.0, max(0.0, float(e.get("confidence") or 0.5)))
        except (TypeError, ValueError):
            conf = 0.5
        evs.append(dict(event_type=e["event_type"], event_date=d, region=_region(e.get("region")), actor=(e.get("actor") or None),
                        missiles=_int(e.get("missiles")), drones=_int(e.get("drones")), guided_bombs=_int(e.get("guided_bombs")),
                        killed=_int(e.get("killed")), injured=_int(e.get("injured")), details=(str(e.get("details") or "")[:200] or None),
                        confidence=conf))
    return sts, evs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default="gemini")
    ap.add_argument("--model", default=None)
    ap.add_argument("--delay", type=float, default=None)
    ap.add_argument("--since", default="2026-03-31")
    ap.add_argument("--random", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.model = a.model or ("claude-haiku-5-5" if a.provider == "anthropic" else "gemini-3.5-flash-lite")
    delay = a.delay if a.delay is not None else (6.0 if a.provider == "gemini" else 0.0)
    key = "ANTHROPIC_API_KEY" if a.provider == "anthropic" else "GEMINI_API_KEY"
    if not os.environ.get(key):
        sys.exit(f"{key} is not set")
    client = None
    if a.provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
    from load_to_db import connect
    sysmsg = system_prompt()
    pv = f"{PROMPT_VERSION}-{a.provider}"
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT m.id, m.text, m.ts_published, m.source_id, s.country, s.type FROM messages m JOIN sources s ON s.id=m.source_id "
                "LEFT JOIN statement_extractions x ON x.message_id = m.id "
                "WHERE x.message_id IS NULL AND m.source_id = ANY(%s) AND length(m.text) >= 120 AND m.ts_published >= %s "
                "AND (s.type IN ('official') OR m.text ~* %s) "
                "ORDER BY " + ("random()" if a.random else "m.ts_published DESC") + " LIMIT %s",
                (SOURCES, a.since, MEDIA_RE, a.limit))
    rows = cur.fetchall()
    print(f"candidates: {len(rows)}")
    ok = bad = quota_hits = 0
    for mid, text, ts, sid, country, stype in rows:
        try:
            cur.execute("SELECT 1")
        except Exception:  # noqa: BLE001
            conn = connect()
            cur = conn.cursor()
        user = json.dumps({"source": {"id": sid, "country": country, "type": stype}, "ts_published": ts.isoformat(),
                           "text": text[:4000]}, ensure_ascii=False)
        try:
            if client:
                resp = client.messages.create(model=a.model, max_tokens=3000, system=sysmsg, messages=[{"role": "user", "content": user}])
                txt = anthropic_text(resp)
            else:
                txt = gemini_call(a.model, sysmsg, user, os.environ["GEMINI_API_KEY"], max_tokens=3000)
                time.sleep(delay)
            sts, evs = clean(parse_json(txt))
        except QuotaExceeded as e:
            quota_hits += 1
            print(f"quota: {str(e)[:300]}")
            if quota_hits >= 2:
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
            print(mid, sid, "|", json.dumps({"statements": sts, "events": [{**e, "event_date": str(e["event_date"])} for e in evs]}, ensure_ascii=False)[:900])
            continue
        for s in sts:
            cur.execute("INSERT INTO statements(message_id, ts_published, actor_side, actor_name, stype, quote, predicted_event, target_region, "
                        "horizon_days, resolvable, model, prompt_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (mid, ts, s["actor_side"], s["actor_name"], s["stype"], s["quote"], s["predicted_event"], s["target_region"],
                         s["horizon_days"], s["resolvable"], a.model, pv))
        for e in evs:
            cur.execute("INSERT INTO extracted_events(message_id, event_type, event_date, region, actor, missiles, drones, guided_bombs, killed, injured, "
                        "details, confidence, model, prompt_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (mid, e["event_type"], e["event_date"], e["region"], e["actor"], e["missiles"], e["drones"], e["guided_bombs"],
                         e["killed"], e["injured"], e["details"], e["confidence"], a.model, pv))
        cur.execute("INSERT INTO statement_extractions(message_id, n_statements, n_events, model, prompt_version) VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (message_id) DO NOTHING", (mid, len(sts), len(evs), a.model, pv))
        conn.commit()
    print(f"extracted {ok}, failed {bad}")
    if bad and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
