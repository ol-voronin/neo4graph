"""Resolve statements (docs/statements_layer.md, docs/factcheck.md): gather evidence posts from official and news sources
in the statement's window that share the most keywords with predicted_event/quote, plus structured ground truth (oblast
summaries, alerts, Air Force waves, RU MoD claims), ask the model for a verdict, store verdict + evidence ids.

Modes (pick one):
  (default)  forecasts / threats / promises whose horizon has passed
  --facts    claims of fact and denials, window -3..+5 days around the statement
  --retry    'unresolvable' ones checked < 3 times, last check > 7 days ago (self-confirmed ones at once): news follow-ups may exist now (window +30 days)
  --redo     re-check already resolved ones (oldest check first)

  python src/resolve_statements.py [--limit 40] [--provider gemini|anthropic] [--facts|--retry|--redo] [--dry-run]
Bulk: src/statements_batch.py submit-resolve (same evidence and prompt through the Message Batches API).
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

# source ids are case-sensitive (tg:<channel as in channels.json>, rss:<feed> from collect_rss.py)
EVIDENCE_SOURCES = ["tg:kpszsu", "tg:GeneralStaffZSU", "tg:V_Zelenskiy_official", "tg:MID_Russia", "tg:mod_russia", "tg:news_kremlin",
                    "tg:medvedev_telegram", "rss:whitehouse", "rss:state_dept", "rss:uk_mod", "rss:un_news", "tg:golosameriki",
                    "tg:ukrpravda_news", "tg:kyivindependent_official", "tg:BBCUkrainian", "tg:bbcrussian", "tg:radiosvoboda",
                    "tg:suspilnenews", "tg:censor_net", "tg:hromadske_ua", "tg:rian_ru", "tg:tass_agency", "tg:meduzalive",
                    "tg:kommersant", "tg:rbc_news", "tg:synegubov", "tg:kharkivoda", "tg:kyivoda", "tg:mykolaiv_ova", "tg:vinnytsiaoda",
                    "rss:bbc", "rss:guardian", "rss:nytimes", "rss:ft", "rss:wsj", "rss:economist", "rss:politico_eu", "rss:dw",
                    "rss:euronews", "rss:aljazeera", "tg:sap_gov_ua", "tg:NAZK_gov_ua", "tg:BihusInfo", "tg:rybar", "tg:boris_rozhin"]
STOP = set("the and for with that this from will was were are has have been not said says would could should about after before "
           "they their there into over under also than then when what which while ukraine russia ukrainian russian "
           "они что это как для при или его её их про над под без від для або та що як при буде було були має мають "
           "не на в у з до по за із і й а но та же ще вже україна україни україні росія росії россия россии украина украины "
           "заявив заявил заявила сказав сказал повідомив сообщил також также".split())
SYSTEM = """Ти — перевіряльник фактів. Тобі дають заяву (прогноз, обіцянку, погрозу або твердження про факт), очікувану подію (що мало статися або що стверджується) і добірку повідомлень і даних із цього періоду як докази.
Визнач лише за цими доказами: підтверджується (true), спростовується (false), частково (partial), чи доказів недостатньо (unresolvable). Заява однієї сторони не є підтвердженням її ж твердження (докази з поміткою same_source — це той самий мовець); шукай незалежні джерела або структуровані дані.
Структуровані дані (id з префіксом gt:) — щоденні зведення: по області (complete=true означає повне зведення: нуль у ньому — справжній нуль), хвилі Повітряних сил по Україні, тривоги. Якщо заява обіцяла/погрожувала подією, яка мала б потрапити в ці зведення (удар по області, масований обстріл), а повні зведення за весь строк її не показують — це false, а не unresolvable. Якщо подія сталася пізніше строку — partial.
unresolvable — лише коли докази справді мовчать про предмет заяви.
true вимагає хоча б одного незалежного доказу (інше джерело або gt:). Якщо заяву підтверджують лише повідомлення самого мовця (same_source) чи переказ його ж слів у медіа — це unresolvable.
Теми. Обмін полоненими або тілами (gt:exchange): обмін, про який повідомили обидві сторони (UA і RU джерела), — незалежне підтвердження; названа кількість, що не збігається з повідомленнями іншої сторони, — partial. Обмін, обіцяний до дати, але не зафіксований до дедлайну, коли повідомлення про обміни за період є, — false. Перемир'я / мораторій (gt:ceasefire, зведення ударів gt:ua і gt:waves, заяви МО РФ gt:ru): твердження «дотримується» спростовують удари відповідного типу (по енергетиці — якщо вказано) у вікні дії; обіцянку перемир'я, яке не почалося, — false. Закони (gt:legal_act): «ухвалить/підпише до дати» підтверджує лише повідомлення про ухвалення чи підпис, не про реєстрацію чи розгляд. Корупція: підозра, затримання, обвинувачення підтверджуються повідомленнями НАБУ/САП/НАЗК/суду; обіцянки покарати — лише процесуальним кроком у строк.
Нічого не додавай зі своїх знань. Не оцінюй намір (брехня чи помилка) — лише відповідність доказам. Поверни ТІЛЬКИ JSON: {"verdict":"true|false|partial|unresolvable","evidence_ids":["..."],"note":"<=200 символів, українською: що саме підтверджує або спростовує"}"""
COLS = "id, ts_published, actor_side, actor_name, stype, quote, predicted_event, target_region, horizon_days, message_id"
TOPIC_FIRST = "(topic IS NOT NULL) DESC, "  # migration 020; dropped by due() before it is applied
_HAS = {}


def has_col(cur, table, col):
    """migration 019 adds statements.n_checks; keep working before it is applied"""
    k = (table, col)
    if k not in _HAS:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s", (table, col))
        _HAS[k] = cur.fetchone() is not None
    return _HAS[k]


def keywords(pred: str, quote: str):
    """Distinctive stems first: capitalised words and numbers from predicted_event, then the quote; 6-letter stems match
    inflected forms (Харкову/Харкова -> харков)."""
    out, seen = [], set()
    for src in (pred or "", quote or ""):
        toks = re.findall(r"[\w'’-]{3,}", src)
        toks = sorted(toks, key=lambda w: (not (w[:1].isupper() or w.isdigit()), 0))  # stable: proper nouns / numbers first
        for w in toks:
            lw = w.lower()
            if lw in STOP or (len(lw) < 4 and not lw.isdigit()):
                continue
            k = lw.replace("'", "").replace("’", "")
            k = k[:max(4, min(6, len(k) - 2))]
            if k not in seen:
                seen.add(k)
                out.append(k)
    return out[:10]


_VOW = {"о": "[оі]", "і": "[іиыо]", "и": "[иіы]", "ы": "[ыи]", "ї": "[їи]", "е": "[еєэ]", "є": "[єе]", "э": "[эе]", "г": "[гґ]", "ґ": "[ґг]"}


def stem_rx(k: str) -> str:
    """Ukrainian and Russian spellings of one stem: харко ~ Харькову ~ Харків, Зеленськ ~ Зеленск (vowel classes, optional ь/'
    after consonants)."""
    out = []
    for c in k.replace("ь", ""):
        out.append(_VOW.get(c, re.escape(c)))
        if c.isalpha() and c in "бвгґджзклмнпрстфхцчшщ":
            out.append("[ь'’]?")
    return "".join(out)


def due(cur, mode, limit, exclude=()):
    nc = has_col(cur, "statements", "n_checks")
    tf = TOPIC_FIRST if has_col(cur, "statements", "topic") else ""
    ex = list(exclude)
    if mode == "facts":
        cur.execute(f"SELECT {COLS} FROM statements WHERE stype IN ('claim_fact','denial') AND status = 'pending' AND id <> ALL(%s) "
                    f"AND ts_published < now() - interval '3 days' ORDER BY {tf}ts_published DESC LIMIT %s", (ex, limit))
    elif mode == "retry":
        cond = "AND n_checks < 3 " if nc else ""
        cur.execute(f"SELECT {COLS} FROM statements WHERE status = 'unresolvable' AND id <> ALL(%s) {cond}"
                    "AND (resolved_at < now() - interval '7 days' OR resolver_note LIKE 'лише джерело мовця:%%') AND ts_published > now() - interval '180 days' "
                    f"AND (resolvable OR stype IN ('claim_fact','denial')) ORDER BY {tf}resolved_at LIMIT %s", (ex, limit))
    elif mode == "redo":
        cur.execute(f"SELECT {COLS} FROM statements WHERE status <> 'pending' AND id <> ALL(%s) ORDER BY resolved_at NULLS FIRST LIMIT %s", (ex, limit))
    else:
        cur.execute(f"SELECT {COLS} FROM statements WHERE resolvable AND status = 'pending' AND id <> ALL(%s) "
                    f"AND ts_published + (horizon_days || ' days')::interval < now() - interval '2 days' ORDER BY {tf}ts_published LIMIT %s", (ex, limit))
    return cur.fetchall()


def window(row, mode):
    ts, stype, horizon = row[1], row[4], row[8]
    fact = stype in ("claim_fact", "denial")
    if fact:
        return ts - timedelta(days=3), ts + timedelta(days=30 if mode == "retry" else 5)
    return ts, ts + timedelta(days=horizon + (30 if mode == "retry" else 3))


def _gt(d, ts):
    return ts.__class__.fromisoformat(f"{d}T12:00:00+03:00")


def topic_gt(cur, topic, region, t0, t1, ts):
    """Structured evidence for the tracked topics: extracted exchange / ceasefire / legal-act events in the window, with the
    reporting side (an exchange reported by both UA and RU sources is confirmed independently), and for ceasefires the
    nationwide strike sums even when the statement names no region."""
    out = []
    etype = {"pow": "prisoner_exchange", "exchange": "prisoner_exchange", "ceasefire": "ceasefire", "law": "legal_act"}.get(topic)
    if etype:
        cur.execute("SELECT e.id, e.event_date, e.details, e.people, s.country, m.source_id FROM extracted_events e JOIN messages m ON m.id = e.message_id "
                    "JOIN sources s ON s.id = m.source_id WHERE e.event_type = %s AND coalesce(e.event_date, m.ts_published::date) BETWEEN %s AND %s "
                    "ORDER BY coalesce(e.event_date, m.ts_published::date) LIMIT 30", (etype, t0.date(), t1.date()))
        for i, d, det, ppl, ctry, src in cur.fetchall():
            out.append((f"gt:{'exchange' if etype == 'prisoner_exchange' else etype}:{i}", f"ground_truth:{etype}", _gt(d or t0.date(), ts),
                        f"{etype} {d or ''}: {det or ''}{f' · людей {ppl}' if ppl else ''} (повідомлення сторони {ctry}, {src})"))
        if not out:
            out.append((f"gt:{etype}:none", f"ground_truth:{etype}", _gt(t0.date(), ts),
                        f"За {t0.date()}…{t1.date()} у витягнутих подіях немає жодного запису типу {etype} (збір міг бути неповним)."))
    if topic == "ceasefire" and region != "ukraine":
        cur.execute("SELECT to_regclass('oblast_daily_gt') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("SELECT date, count(*) FILTER (WHERE attacked), sum(coalesce(missiles,0)), sum(coalesce(drones,0)) FROM oblast_daily_gt "
                        "WHERE date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1", (t0.date(), t1.date()))
            for d, n_att, mi, dr in cur.fetchall()[:45]:
                out.append((f"gt:ua:{d}", "ground_truth:oblast_daily", _gt(d, ts), f"По Україні за {d}: атаковано областей {n_att}, ракет {mi}, дронів {dr} (сума обласних зведень)"))
    return out


def gather(cur, conn, row, mode):
    """Evidence rows (id, source, ts, text): keyword-ranked posts (>= 2 shared stems when there are 2+), own post excluded,
    same speaker's channel marked; then structured ground truth for the region and window."""
    sid, ts, side, name, stype, quote, pred, region, horizon, msg_id = row
    kws = keywords(pred, quote)
    t0, t1 = window(row, mode)
    ev = []
    if kws:
        need = 2 if len(kws) >= 3 else 1
        rx = [stem_rx(k) for k in kws]
        cur.execute("SELECT source_id FROM messages WHERE id = %s", (msg_id,))
        own_src = (cur.fetchone() or [None])[0]
        cur.execute("SELECT m.id, m.source_id, m.ts_published, left(regexp_replace(m.text, '[[:space:]]+', ' ', 'g'), 700), "
                    "(SELECT count(*) FROM unnest(%s::text[]) k WHERE m.text ~* k) hits "
                    "FROM messages m WHERE m.source_id = ANY(%s) AND m.ts_published BETWEEN %s AND %s AND m.text ~* %s AND m.id <> %s "
                    "ORDER BY hits DESC, m.ts_published LIMIT 40", (rx, EVIDENCE_SOURCES, t0, t1, "|".join(rx), msg_id))
        rows = [r for r in cur.fetchall() if r[4] >= need][:14]
        ev = [(i, s + (" (same_source)" if s == own_src else ""), t, x) for i, s, t, x, _ in sorted(rows, key=lambda r: r[2])]
    try:
        cur.execute("SELECT to_regclass('oblast_daily_gt') IS NOT NULL")
        gt = cur.fetchone()[0]
        if gt and region and region not in ("ukraine", "russia") and not region.startswith("russia"):
            cur.execute("SELECT date, missiles, drones, kab, attacked, complete, alert_n, sources FROM oblast_daily_gt WHERE region_id = %s "
                        "AND date BETWEEN %s AND %s ORDER BY date", (region, t0.date(), t1.date()))
            days = cur.fetchall()
            for d, mi, dr, kab, att, comp, al, srcs in days[:45]:
                ev.append((f"gt:oblast:{region}:{d}", "ground_truth:oblast_daily", _gt(d, ts),
                           f"Зведення {region} за {d}: ракети {mi}, дрони {dr}, КАБ {kab}, атаковано: {att}, complete={comp}, тривог {al} (джерела: {', '.join(srcs or [])})"))
            if not days:
                ev.append((f"gt:oblast:{region}:none", "ground_truth:oblast_daily", _gt(t0.date(), ts),
                           f"За {t0.date()}…{t1.date()} у зведеннях по {region} немає жодного дня з даними (збір міг бути неповним)."))
        elif gt and region == "ukraine":
            cur.execute("SELECT date, count(*) FILTER (WHERE attacked), sum(coalesce(missiles,0)), sum(coalesce(drones,0)) FROM oblast_daily_gt "
                        "WHERE date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1", (t0.date(), t1.date()))
            for d, n_att, mi, dr in cur.fetchall()[:45]:
                ev.append((f"gt:ua:{d}", "ground_truth:oblast_daily", _gt(d, ts), f"По Україні за {d}: атаковано областей {n_att}, ракет {mi}, дронів {dr} (сума обласних зведень)"))
        elif not gt:
            cur.execute("SELECT date, missiles, drones, guided_bombs, city_attacked FROM kharkiv_daily WHERE date BETWEEN %s AND %s ORDER BY date", (t0.date(), t1.date()))
            for d, mi, dr, kab, city in cur.fetchall():
                ev.append((f"gt:kharkiv:{d}", "ground_truth:kharkiv_daily", _gt(d, ts), f"Зведення по Харківщині за {d}: ракети {mi}, дрони {dr}, КАБ {kab}, місто атаковано: {city}"))
        cur.execute("SELECT date, max(launched_missiles), max(launched_drones) FROM waves WHERE date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1", (t0.date(), t1.date()))
        for d, lm, ld in cur.fetchall()[:45]:
            ev.append((f"gt:waves:{d}", "ground_truth:air_force_summary", _gt(d, ts), f"Повітряні сили за {d}: запущено ракет {lm}, дронів {ld}"))
        topic = None
        if has_col(cur, "statements", "topic"):
            cur.execute("SELECT topic FROM statements WHERE id = %s", (sid,))
            topic = (cur.fetchone() or [None])[0]
        ev += topic_gt(cur, topic, region, t0, t1, ts)
        if (region or "").startswith("russia") or side.startswith("RU") or topic == "ceasefire":
            cur.execute("SELECT date, drones FROM ru_attacks_daily WHERE date BETWEEN %s AND %s ORDER BY date", (t0.date(), t1.date()))
            for d, dr in cur.fetchall()[:45]:
                ev.append((f"gt:ru:{d}", "ground_truth:ru_mod_claims", _gt(d, ts), f"МО РФ заявило про {dr} перехоплених українських дронів за {d} (заява сторони, не підтвердження)"))
    except Exception:  # noqa: BLE001
        conn.rollback()
    return ev


def payload(row, ev):
    sid, ts, side, name, stype, quote, pred, region, horizon, _ = row
    return json.dumps({"statement": {"actor": f"{side} / {name}", "type": stype, "quote": quote, "said_at": ts.isoformat(),
                                     "predicted_event": pred, "deadline": (ts + timedelta(days=horizon)).date().isoformat(), "region": region},
                       "evidence": [{"id": i, "source": s, "ts": t.isoformat(), "text": x} for i, s, t, x in ev]}, ensure_ascii=False)


def apply(cur, sid, r, ev_ids):
    verdict = r.get("verdict") if r.get("verdict") in ("true", "false", "partial", "unresolvable") else "unresolvable"
    ids = [str(i) for i in (r.get("evidence_ids") or []) if str(i) in set(ev_ids)]
    note = (r.get("note") or "")[:300]
    if verdict in ("true", "partial") and not independent(cur, sid, ids):
        # the speaker confirming themselves is not evidence (most early 'true' verdicts were the actor's own channel)
        verdict, note = "unresolvable", ("лише джерело мовця: " + note)[:300]
    nc = ", n_checks = n_checks + 1" if has_col(cur, "statements", "n_checks") else ""
    cur.execute(f"UPDATE statements SET status=%s, resolved_at=now(), evidence_ids=%s, resolver_note=%s{nc} WHERE id=%s",
                (verdict, ids, note, sid))
    return verdict


def independent(cur, sid, ids) -> bool:
    """At least one cited evidence item from outside the statement's own channel: structured ground truth (gt:) or a post
    from another source. Mirrors the backfill in db/migrations/020_topics_mood.sql."""
    if any(i.startswith("gt:") for i in ids):
        return True
    cur.execute("SELECT EXISTS (SELECT 1 FROM messages e, statements s JOIN messages m ON m.id = s.message_id "
                "WHERE s.id = %s AND e.id = ANY(%s) AND e.source_id <> m.source_id)", (sid, ids))
    return cur.fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default="gemini")
    ap.add_argument("--model", default=None)
    ap.add_argument("--dry-run", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--redo", action="store_true", help="re-check already resolved statements (oldest check first)")
    g.add_argument("--facts", action="store_true", help="claims of fact / denials, window -3..+5 days")
    g.add_argument("--retry", action="store_true", help="re-check 'unresolvable' ones with a wider window")
    a = ap.parse_args()
    mode = "facts" if a.facts else "retry" if a.retry else "redo" if a.redo else "due"
    a.model = a.model or ("claude-haiku-5-5" if a.provider == "anthropic" else "gemini-3.5-flash-lite")
    key = "ANTHROPIC_API_KEY" if a.provider == "anthropic" else "GEMINI_API_KEY"
    if not os.environ.get(key):
        sys.exit(f"{key} is not set")
    client = claude() if a.provider == "anthropic" else None
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    rows = due(cur, mode, a.limit)
    print(f"{mode}: {len(rows)}")
    ok = bad = quota_hits = 0
    for row in rows:
        ev = gather(cur, conn, row, mode)
        try:
            if client:
                txt = anthropic_text(client.messages.create(model=a.model, max_tokens=3000, output_config={"effort": "low"}, system=SYSTEM,
                                                            messages=[{"role": "user", "content": payload(row, ev)}]))
            else:
                txt = gemini_call(a.model, SYSTEM, payload(row, ev), os.environ["GEMINI_API_KEY"], max_tokens=600)
                time.sleep(6)
            r = parse_json(txt)
        except QuotaExceeded as e:
            quota_hits += 1
            print(f"quota: {str(e)[:300]}")
            if quota_hits >= 2:
                break
            time.sleep(65)
            continue
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"skip {row[0]}: {e}")
            continue
        quota_hits = 0
        ok += 1
        if a.dry_run:
            print(row[0], row[2], row[4], "|", (row[6] or "")[:80], "->", r.get("verdict"), f"({len(ev)} evidence)", (r.get("note") or "")[:120])
            continue
        apply(cur, row[0], r, [e[0] for e in ev])
        conn.commit()
    print(f"resolved {ok}, failed {bad}")


if __name__ == "__main__":
    main()
