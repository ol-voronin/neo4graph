"""Position changes: for each actor, find pairs of statements (or statement vs. action) that contradict each other over
time: a promise later reversed, a claim later denied by the same actor, words vs. deeds. Stored in contradictions with
both quotes, dates and links; shown in the Фактчек tab as 'зміна позиції'. Judges consistency only, never intent.

  python src/find_contradictions.py [--provider anthropic|gemini] [--max-actors 20] [--since 2026-03-31]
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import anthropic_text, gemini_call, parse_json  # noqa: E402

SYSTEM = """Ти аналізуєш заяви ОДНОГО актора (та його дії) у хронологічному порядку й шукаєш суперечності: обіцянка, яку пізніше заперечили або зробили протилежне; твердження, яке той самий актор згодом спростував; слова, що розходяться з діями.
Поверни ТІЛЬКИ JSON: {"pairs":[{"a_id":<id раннього>,"b_id":<id пізнішого>,"kind":"reversal|contradiction|say_vs_do","topic":"<=80 символів","explanation":"<=200 символів, українською, що саме суперечить","confidence":0.0}]}
Правила: лише явні суперечності по суті (не різниця тону чи деталей, не уточнення); обидві цитати мають стосуватися одного предмета; не вигадуй; якщо суперечностей немає — порожній список."""


def actor_key(side, name):
    n = re.sub(r"[^\w\s'’-]", " ", (name or "").lower())
    n = re.sub(r"\s+", " ", n).strip()
    if not n:
        return side
    # institution vs person: keep first two tokens for persons, whole for institutions
    toks = n.split()
    return side + "|" + (" ".join(toks[:2]) if len(toks) <= 3 else n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="gemini")
    ap.add_argument("--max-actors", type=int, default=20)
    ap.add_argument("--since", default="2026-03-31")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    key = "ANTHROPIC_API_KEY" if a.provider == "anthropic" else "GEMINI_API_KEY"
    if not os.environ.get(key):
        sys.exit(f"{key} is not set")
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS contradictions (id bigserial PRIMARY KEY, actor_key text, actor_side text, actor_name text, a_statement bigint REFERENCES statements(id) ON DELETE CASCADE, "
                "b_statement bigint REFERENCES statements(id) ON DELETE CASCADE, b_event bigint, kind text, topic text, explanation text, confidence real, model text, created_at timestamptz DEFAULT now(), "
                "UNIQUE (a_statement, b_statement, b_event))")
    cur.execute("SELECT id, ts_published, actor_side, actor_name, stype, quote, predicted_event, target_region, status FROM statements WHERE ts_published >= %s ORDER BY ts_published", (a.since,))
    groups = defaultdict(list)
    for r in cur.fetchall():
        groups[actor_key(r[2], r[3])].append(r)
    cur.execute("SELECT id, event_date, event_type, actor, details, region FROM extracted_events WHERE event_date >= %s AND event_type <> 'strike' ORDER BY event_date", (a.since,))
    events = cur.fetchall()
    cands = sorted(((k, v) for k, v in groups.items() if len(v) >= 2), key=lambda kv: -len(kv[1]))[: a.max_actors]
    print(f"actors with >=2 statements: {len(cands)}")
    total = 0
    for k, sts in cands:
        side, name = sts[0][2], sts[0][3]
        acts = [e for e in events if e[3] and name and name.split()[0].lower() in (e[3] or "").lower()][:15]
        payload = {"actor": {"side": side, "name": name},
                   "statements": [{"id": s[0], "date": s[1].date().isoformat(), "type": s[4], "quote": s[5], "predicted_event": s[6], "region": s[7], "verdict": s[8]} for s in sts[-40:]],
                   "actions": [{"event_id": e[0], "date": str(e[1]), "type": e[2], "details": e[4], "region": e[5]} for e in acts]}
        user = json.dumps(payload, ensure_ascii=False)
        try:
            if a.provider == "anthropic":
                import anthropic
                txt = anthropic_text(anthropic.Anthropic().messages.create(model="claude-haiku-5-5", max_tokens=3000, output_config={"effort": "low"},
                                                                         system=SYSTEM, messages=[{"role": "user", "content": user}]))
            else:
                txt = gemini_call("gemini-3.5-flash-lite", SYSTEM, user, os.environ["GEMINI_API_KEY"], max_tokens=3000)
            pairs = (parse_json(txt) or {}).get("pairs") or []
        except Exception as e:  # noqa: BLE001
            print(f"skip {k}: {e}")
            continue
        ids = {s[0] for s in sts}
        n = 0
        for p in pairs:
            try:
                aid, bid = int(p.get("a_id")), int(p.get("b_id"))
            except (TypeError, ValueError):
                continue
            if aid not in ids or (bid not in ids and p.get("kind") != "say_vs_do"):
                continue
            b_stmt = bid if bid in ids else None
            b_evt = None if bid in ids else bid
            if a.dry_run:
                print(f"  {name}: {p.get('kind')} | {p.get('topic')} | {p.get('explanation')}")
                n += 1
                continue
            cur.execute("INSERT INTO contradictions(actor_key, actor_side, actor_name, a_statement, b_statement, b_event, kind, topic, explanation, confidence, model) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (a_statement, b_statement, b_event) DO UPDATE SET explanation=EXCLUDED.explanation, confidence=EXCLUDED.confidence",
                        (k, side, name, aid, b_stmt, b_evt, p.get("kind") if p.get("kind") in ("reversal", "contradiction", "say_vs_do") else "contradiction",
                         (p.get("topic") or "")[:120], (p.get("explanation") or "")[:300], float(p.get("confidence") or 0.5), "claude-haiku-5-5" if a.provider == "anthropic" else "gemini"))
            n += 1
        conn.commit()
        total += n
        print(f"{name or side}: {len(sts)} statements, {len(acts)} actions -> {n} contradictions")
    print(f"total contradictions: {total}")


if __name__ == "__main__":
    main()
