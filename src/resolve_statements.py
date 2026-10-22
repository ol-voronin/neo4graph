"""Resolve statements whose horizon has passed (docs/statements_layer.md): gather evidence posts from official and
news sources inside [ts, ts + horizon + 3 days] that share keywords with predicted_event (and the region), ask the
model for a verdict, store verdict + evidence ids. Verdicts can be overwritten by a later pass (--redo).

Usage: python src/resolve_statements.py [--limit 40] [--provider gemini|anthropic] [--dry-run] [--redo]
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import QuotaExceeded, anthropic_text, gemini_call, parse_json, claude  # noqa: E402

EVIDENCE_SOURCES = ["tg:kpszsu", "tg:GeneralStaffZSU", "tg:V_Zelenskiy_official", "tg:MID_Russia", "tg:mod_russia", "tg:news_kremlin",
                    "rss:whitehouse", "tg:golosameriki", "tg:ukrpravda_news", "tg:kyivindependent", "tg:bbcukrainian", "tg:bbcrussian",
                    "tg:radiosvoboda", "tg:suspilnenews", "tg:censor_net", "tg:hromadske_ua", "tg:rian_ru", "tg:tass_agency",
                    "tg:meduzalive", "tg:synegubov", "tg:kharkivoda", "tg:kyivoda"]
STOP = set("the and for with that this from will was were are has have been not они они что это как для при или его её их про над под без від для або та що як при буде було були має мають не на в у з до по за із і й а но та же ще вже".split())
SYSTEM = """Ти — перевіряльник фактів. Тобі дають заяву (прогноз, обіцянку, погрозу або твердження про факт), очікувану подію (що мало статися або що стверджується) і добірку повідомлень і даних із цього періоду як докази.
Визнач лише за цими доказами: підтверджується (true), спростовується (false), частково (partial), чи доказів недостатньо (unresolvable). Заява однієї сторони не є підтвердженням її ж твердження; шукай незалежні джерела або структуровані дані.
Нічого не додавай зі своїх знань. Не оцінюй намір (брехня чи помилка) — лише відповідність доказам. Поверни ТІЛЬКИ JSON: {"verdict":"true|false|partial|unresolvable","evidence_ids":["..."],"note":"<=200 символів, українською: що саме підтверджує або спростовує"}"""


def keywords(s: str):
    words = re.findall(r"[\w'’-]{4,}", (s or "").lower())
    return [w for w in words if w not in STOP][:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default="gemini")
    ap.add_argument("--model", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--redo", action="store_true", help="also re-check already resolved statements (oldest first)")
    ap.add_argument("--facts", action="store_true", help="fact-check claims of fact / denials right away (window -3..+5 days), instead of due forecasts")
    a = ap.parse_args()
    a.model = a.model or ("claude-haiku-5-5" if a.provider == "anthropic" else "gemini-3.5-flash-lite")
    key = "ANTHROPIC_API_KEY" if a.provider == "anthropic" else "GEMINI_API_KEY"
    if not os.environ.get(key):
        sys.exit(f"{key} is not set")
    client = None
    if a.provider == "anthropic":
        import anthropic
        client = claude()
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cond = "status <> 'pending'" if a.redo else "status = 'pending'"
    if a.facts:
        cur.execute(f"SELECT id, ts_published, actor_side, actor_name, stype, quote, predicted_event, target_region, horizon_days FROM statements "
                    f"WHERE stype IN ('claim_fact','denial') AND {cond} AND ts_published < now() - interval '3 days' "
                    f"ORDER BY resolved_at NULLS FIRST, ts_published DESC LIMIT %s", (a.limit,))
    else:
        cur.execute(f"SELECT id, ts_published, actor_side, actor_name, stype, quote, predicted_event, target_region, horizon_days FROM statements "
                    f"WHERE resolvable AND {cond} AND ts_published + (horizon_days || ' days')::interval < now() - interval '2 days' "
                    f"ORDER BY resolved_at NULLS FIRST, ts_published LIMIT %s", (a.limit,))
    rows = cur.fetchall()
    print(f"due: {len(rows)}")
    ok = bad = quota_hits = 0
    for sid, ts, side, name, stype, quote, pred, region, horizon in rows:
        kws = keywords((pred or "") + " " + quote)
        if region and region not in ("ukraine", "russia"):
            kws.append(region.split("-")[0][:6])
        pattern = "|".join(re.escape(k[:7]) for k in kws) or "zzzz"
        t0, t1 = (ts - timedelta(days=3), ts + timedelta(days=5)) if a.facts else (ts, ts + timedelta(days=horizon + 3))
        cur.execute("SELECT id, source_id, ts_published, left(text, 700) FROM messages WHERE source_id = ANY(%s) AND ts_published BETWEEN %s AND %s "
                    "AND text ~* %s ORDER BY ts_published LIMIT 12", (EVIDENCE_SOURCES, t0, t1, pattern))
        ev = cur.fetchall()
        # structured ground truth around the statement date (strikes, intercept claims) as extra evidence rows
        try:
            cur.execute("SELECT to_regclass('oblast_daily_gt') IS NOT NULL")
            if cur.fetchone()[0] and region and region not in ("ukraine",) and not region.startswith("russia"):
                # per-oblast ground truth (migration 015): administration summaries + extracted strike events
                cur.execute("SELECT date, missiles, drones, kab, attacked, sources FROM oblast_daily_gt WHERE region_id = %s AND date BETWEEN %s AND %s "
                            "AND (attacked OR missiles IS NOT NULL OR drones IS NOT NULL) ORDER BY date", (region, t0.date(), t1.date()))
                for d, mi, dr, kab, att, srcs in cur.fetchall():
                    ev.append((f"gt:oblast:{region}:{d}", "ground_truth:oblast_daily", ts.__class__.fromisoformat(f"{d}T12:00:00+03:00"),
                               f"Зведення по області {region} за {d}: ракети {mi}, дрони {dr}, КАБ {kab}, атаковано: {att} (джерела: {', '.join(srcs or [])})"))
            else:
                cur.execute("SELECT date, missiles, drones, guided_bombs, city_attacked FROM kharkiv_daily WHERE date BETWEEN %s AND %s ORDER BY date", (t0.date(), t1.date()))
                for d, mi, dr, kab, city in cur.fetchall():
                    ev.append((f"gt:kharkiv:{d}", "ground_truth:kharkiv_daily", ts.__class__.fromisoformat(f"{d}T12:00:00+03:00"),
                               f"Зведення по Харківщині за {d}: ракети {mi}, дрони {dr}, КАБ {kab}, місто атаковано: {city}"))
            cur.execute("SELECT date, drones FROM ru_attacks_daily WHERE date BETWEEN %s AND %s ORDER BY date", (t0.date(), t1.date()))
            for d, dr in cur.fetchall():
                ev.append((f"gt:ru:{d}", "ground_truth:ru_mod_claims", ts.__class__.fromisoformat(f"{d}T12:00:00+03:00"), f"МО РФ заявило про {dr} перехоплених українських дронів за {d} (заява сторони, не підтвердження)"))
            cur.execute("SELECT date, launched_missiles, launched_drones FROM waves WHERE date BETWEEN %s AND %s ORDER BY date", (t0.date(), t1.date()))
            for d, lm, ld in cur.fetchall():
                ev.append((f"gt:waves:{d}", "ground_truth:air_force_summary", ts.__class__.fromisoformat(f"{d}T12:00:00+03:00"), f"Повітряні сили за {d}: запущено ракет {lm}, дронів {ld}"))
        except Exception as e:  # noqa: BLE001
            conn.rollback()
        user = json.dumps({"statement": {"actor": f"{side} / {name}", "type": stype, "quote": quote, "said_at": ts.isoformat(),
                                         "predicted_event": pred, "deadline": (ts + timedelta(days=horizon)).date().isoformat(),
                                         "region": region},
                           "evidence": [{"id": i, "source": s, "ts": t.isoformat(), "text": x} for i, s, t, x in ev]}, ensure_ascii=False)
        try:
            if client:
                txt = anthropic_text(client.messages.create(model=a.model, max_tokens=3000, output_config={"effort": "low"}, system=SYSTEM, messages=[{"role": "user", "content": user}]))
            else:
                txt = gemini_call(a.model, SYSTEM, user, os.environ["GEMINI_API_KEY"], max_tokens=600)
                time.sleep(6)
            r = parse_json(txt)
            verdict = r.get("verdict") if r.get("verdict") in ("true", "false", "partial", "unresolvable") else "unresolvable"
            ids = [str(i) for i in (r.get("evidence_ids") or []) if str(i) in {e[0] for e in ev}]
        except QuotaExceeded as e:
            quota_hits += 1
            print(f"quota: {str(e)[:300]}")
            if quota_hits >= 2:
                break
            time.sleep(65)
            continue
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"skip {sid}: {e}")
            continue
        quota_hits = 0
        ok += 1
        if a.dry_run:
            print(sid, side, stype, "|", (pred or "")[:80], "->", verdict, f"({len(ev)} evidence)", (r.get("note") or "")[:120])
            continue
        cur.execute("UPDATE statements SET status=%s, resolved_at=now(), evidence_ids=%s, resolver_note=%s WHERE id=%s",
                    (verdict, ids, (r.get("note") or "")[:300], sid))
        conn.commit()
    print(f"resolved {ok}, failed {bad}")


if __name__ == "__main__":
    main()
