"""Topic context for the fact-check page (migration 032): per topic (and '' = all) a short brief of the last 30 days written
by the model from our own data only: the topic's statements (with verdicts) and the extracted events of matching types.
Every event / checked item cites a ref (s<statement id> or e<event id>) from that data; items citing unknown refs are dropped.

  python src/topic_briefs.py build [--topic exchange] [--days 30] [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import anthropic_text, parse_json, claude  # noqa: E402
from topics import TOPICS, _CLASSES  # noqa: E402

MODEL = "claude-haiku-5-5"
NAMES = {"": "усі теми", "pow": "полонені", "exchange": "обміни полоненими й тілами", "ceasefire": "перемир'я, мораторії",
         "tcc": "ТЦК і мобілізація", "law": "закони й рішення влади", "corruption": "корупція"}
# extracted_events types that belong to a topic; for '' the big-picture ones
EVENT_TYPES = {"": ["negotiation", "ceasefire", "prisoner_exchange", "legal_act", "sanction", "aid", "weapons_delivery"],
               "pow": ["prisoner_exchange"], "exchange": ["prisoner_exchange"], "ceasefire": ["ceasefire", "negotiation"],
               "tcc": ["mobilisation", "legal_act"], "law": ["legal_act"], "corruption": ["legal_act", "other"]}
SYSTEM = """Ти — редактор аналітичної довідки. Тобі дають тему, заяви сторін за період (з вердиктами фактчеку) і події, витягнуті з новин.
Напиши контекст теми для читача, який нічого не знає: що відбувається, ключові події, позиції сторін, що перевірено.
Правила: лише з наданих даних, нічого зі своїх знань; дати — з даних; кожна подія і кожен пункт «перевірено» посилається на ref з даних (s123 або e456).
Не вигадуй цифр. Нейтральний тон, українською. Якщо даних мало — так і скажи коротко.
Поверни ТІЛЬКИ JSON:
{"summary":"2–4 речення: що відбувається в темі за період",
 "events":[{"date":"YYYY-MM-DD","text":"<=140 символів","ref":"e…|s…"}],   // 4–8 найважливіших, від нових до старих
 "sides":{"UA":"<=200 символів: що кажуть українські офіційні","RU":"<=200: російські","other":"<=200: США, ЄС, інші; порожньо якщо нема"},
 "checked":[{"ref":"s…","text":"<=160 символів: що заявили і що показала перевірка"}],   // 0–4, найпоказовіші вердикти
 "watch":"<=160 символів: що чекає перевірки або на що зважати далі"}"""


def topic_cond(topic, alias=""):
    p = alias + "." if alias else ""
    return ("TRUE", []) if not topic else (f"{p}topic = %s", [topic])


def gather(cur, topic, days):
    cond, args = topic_cond(topic, "s")
    cur.execute(f"""SELECT s.id, s.ts_published::date, s.actor_name, coalesce(g.country, s.actor_side), s.stype, s.quote, s.predicted_event,
          s.status, s.resolver_note, m.url, m.source_id
        FROM statements s JOIN messages m ON m.id = s.message_id
        LEFT JOIN statements_x g ON g.id = s.id
        WHERE {cond} AND s.ts_published > now() - make_interval(days => %s)
        ORDER BY (s.status IN ('true','false','partial')) DESC, (g.actor_id IS NOT NULL) DESC, s.ts_published DESC LIMIT 90""", args + [days])
    st = cur.fetchall()
    rx = _CLASSES.get(topic)
    ev_cond = "e.event_type = ANY(%s)" + (" AND (e.details ~* %s OR m.text ~* %s)" if rx else "")
    ev_args = [EVENT_TYPES[topic]] + ([rx, rx] if rx else [])
    cur.execute(f"""SELECT DISTINCT ON (e.event_date, left(e.details, 50)) e.id, e.event_date, e.event_type, e.actor, e.region, e.details, e.people,
          m.url, m.source_id
        FROM extracted_events e JOIN messages m ON m.id = e.message_id
        WHERE {ev_cond} AND e.event_date > now()::date - %s AND e.details IS NOT NULL
        ORDER BY e.event_date DESC, left(e.details, 50), e.confidence DESC NULLS LAST LIMIT 120""", ev_args + [days])
    ev = cur.fetchall()
    cur.execute(f"""SELECT count(*) FILTER (WHERE s.ts_published > now() - make_interval(days => %s)),
          count(*) FILTER (WHERE s.ts_published <= now() - make_interval(days => %s) AND s.ts_published > now() - make_interval(days => %s * 2)),
          count(*) FILTER (WHERE s.status = 'true' AND s.ts_published > now() - make_interval(days => %s)),
          count(*) FILTER (WHERE s.status = 'false' AND s.ts_published > now() - make_interval(days => %s)),
          count(*) FILTER (WHERE s.status = 'partial' AND s.ts_published > now() - make_interval(days => %s)),
          count(*) FILTER (WHERE s.status = 'unresolvable' AND s.ts_published > now() - make_interval(days => %s))
        FROM statements s WHERE {cond}""", [days] * 3 + [days] * 4 + args)
    n, n_prev, t, f, p, u = cur.fetchone()
    cur.execute(f"""SELECT coalesce(a.name_uk, x.actor_name), count(*) FROM statements_x x LEFT JOIN actors a ON a.id = x.actor_id
        WHERE {topic_cond(topic, 'x')[0]} AND x.ts_published > now() - make_interval(days => %s) AND x.actor_name IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 6""", topic_cond(topic)[1] + [days])
    actors = [{"name": a, "n": k} for a, k in cur.fetchall()]
    stats = {"n": n, "n_prev": n_prev, "actors": actors, "verdicts": {"t": t, "f": f, "p": p, "u": u}}
    return st, ev, stats


def payload(topic, days, st, ev):
    lines = [f"Тема: {NAMES[topic]}. Період: останні {days} днів.", "", "ЗАЯВИ (ref | дата | хто | країна | тип | цитата | очікувана подія | вердикт | висновок перевірки):"]
    for i, d, who, c, stype, q, pe, status, note, *_ in st:
        lines.append(" | ".join(str(x or "") for x in (f"s{i}", d, who, c, stype, (q or "")[:260], (pe or "")[:160], status, (note or "")[:200])))
    lines += ["", "ПОДІЇ З НОВИН (ref | дата | тип | хто | регіон | що | люди):"]
    for i, d, et, actor, reg, det, people, *_ in ev:
        lines.append(" | ".join(str(x or "") for x in (f"e{i}", d, et, actor, reg, (det or "")[:240], json.dumps(people, ensure_ascii=False)[:120] if people else "")))
    return "\n".join(lines)


def clean(body, refs):
    """Drop items that cite refs absent from the data; keep the shape the page expects."""
    ok = lambda r: isinstance(r, str) and r in refs  # noqa: E731
    out = {"summary": str(body.get("summary") or "")[:700],
           "events": [{"date": str(e.get("date", ""))[:10], "text": str(e.get("text", ""))[:200], "ref": e["ref"]}
                      for e in body.get("events") or [] if isinstance(e, dict) and ok(e.get("ref"))][:8],
           "sides": {k: str((body.get("sides") or {}).get(k) or "")[:260] for k in ("UA", "RU", "other")},
           "checked": [{"ref": c["ref"], "text": str(c.get("text", ""))[:220]} for c in body.get("checked") or []
                       if isinstance(c, dict) and ok(c.get("ref")) and c["ref"].startswith("s")][:4],
           "watch": str(body.get("watch") or "")[:220]}
    return out


def build(a):
    from load_to_db import connect
    with connect() as conn:
        cur = conn.cursor()
        for topic in ([a.topic] if a.topic is not None else ["", *TOPICS]):
            st, ev, stats = gather(cur, topic, a.days)
            refs = {f"s{r[0]}": {"url": r[9], "date": str(r[1]), "source": r[10], "status": r[7]} for r in st}
            refs.update({f"e{r[0]}": {"url": r[7], "date": str(r[1]), "source": r[8]} for r in ev})
            if not st and not ev:
                print(f"[{topic or 'all'}] no data, skipped")
                continue
            msg = payload(topic, a.days, st, ev)
            if a.dry_run:
                print(f"[{topic or 'all'}] statements {len(st)}, events {len(ev)}, ~{len(msg) // 3:,} tokens; {stats}")
                continue
            resp = claude().messages.create(model=MODEL, max_tokens=4000, output_config={"effort": "medium"},
                                            system=SYSTEM, messages=[{"role": "user", "content": msg}])
            body = clean(parse_json(anthropic_text(resp)), refs)
            used = {e["ref"] for e in body["events"]} | {c["ref"] for c in body["checked"]}
            cur.execute("""INSERT INTO topic_briefs (topic, generated_at, days, body, stats, refs, model) VALUES (%s, now(), %s, %s, %s, %s, %s)
                ON CONFLICT (topic) DO UPDATE SET generated_at = now(), days = EXCLUDED.days, body = EXCLUDED.body, stats = EXCLUDED.stats,
                  refs = EXCLUDED.refs, model = EXCLUDED.model""",
                        (topic, a.days, json.dumps(body, ensure_ascii=False), json.dumps(stats, ensure_ascii=False),
                         json.dumps({k: refs[k] for k in used}, ensure_ascii=False), MODEL))
            conn.commit()
            print(f"[{topic or 'all'}] {len(body['events'])} events, {len(body['checked'])} checked; {body['summary'][:120]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--topic", choices=["", *TOPICS], default=None)
    b.add_argument("--days", type=int, default=30)
    b.add_argument("--dry-run", action="store_true")
    b.set_defaults(fn=build)
    a = ap.parse_args()
    a.fn(a)
