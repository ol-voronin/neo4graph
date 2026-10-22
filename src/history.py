"""Rhetoric history before the live panel start (2026-03-31) without storing raw posts in Neon.

Raw posts of the 12 panel channels are collected from t.me/s into data/history/*.jsonl, packed into Parquet and
published as the GitHub release `history-latest`. Neon only gets the daily aggregates (`rhetoric_daily_hist`,
migration 013): a few thousand rows, the same columns features.rhetoric() computes live.

  python src/history.py collect --channel rybar --before 79095 --since 2025-01-01   # resumable, below the live range
  python src/history.py pack                                                        # data/history/*.jsonl -> panel_posts.parquet
  python src/history.py aggregate [--dry-run]                                       # parquet -> rhetoric_daily_hist (cutoffs 15, 21, 24)
  python src/history.py verify                                                      # python rules vs the SQL view on live panel posts

Rules: RULES below are a line-for-line copy of the rule_features / rule_labels views (db/migrations/004_rule_features.sql).
`verify` recomputes the live window (Apr–Oct 2026) in Python and compares it with SQL; any rule change must keep both in sync.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from features import ENVOY_RE, IRAN_RE, PANEL, PANEL_START, PEACE_RE  # noqa: E402

HIST = ROOT / "data" / "history"
PARQUET = HIST / "panel_posts.parquet"
CUTOFFS = (15, 21, 24)
# sources.country of the panel channels (none of them is type 'osint', so the live osint filter is a no-op here)
COUNTRY = {"tg:MID_Russia": "RU", "tg:rybar": "RU", "tg:dva_majors": "RU", "tg:boris_rozhin": "RU", "tg:voenkorKotenok": "RU",
           "tg:sashakots": "RU", "tg:wargonzo": "RU", "tg:RVvoenkor": "RU", "tg:meduzalive": "RU",
           "tg:V_Zelenskiy_official": "UA", "tg:GeneralStaffZSU": "UA", "tg:ukrpravda_news": "UA"}

# --- db/migrations/004_rule_features.sql, verbatim patterns ---
RULES = {
    "r_threat": r"(удар|ракет|обстріл|обстрел|знищ|уничтож|ядерн|орешник|орєшнік|oreshnik|масован|массирован|відплат|возмезд|ответн|ультиматум|retaliat|strike|missile|nuclear|annihilat|сотр[её]м|стерти)",
    "r_target": r"(харк|kharkiv|харьков|київ|киев|kyiv|одес|odes|дніпр|днепр|львів|львов|запор|енергет|энергет|infrastructure|інфраструктур|инфраструктур|decision.?making cent|центр.{0,10}(прийняття|принятия))",
    "r_time": r"(сьогодні|сегодня|завтра|найближч|ближайш|на цьому тижні|на этой неделе|скоро|невдовзі|tonight|tomorrow|within|soon|in the coming|через \d+|протягом \d+|в течение \d+)",
    "r_scheduled": r"(переговор|саміт|саммит|summit|talks|годовщин|вибори|выборы|election|парад|мюнхен|munich|рада безпеки|совбез)",
    "r_operational": r"(повітряна тривога|воздушная тревога|пуск|бпла|шахед|shahed|ракета з|курс на|напрямку|направлении)",
}
DDL = """
CREATE TABLE IF NOT EXISTS rhetoric_daily_hist (
  cutoff_hour smallint NOT NULL, d date NOT NULL,
  posts_p int, posts_ru int, thr_ru real, l2_ru real, tgt_ru real, thr_ua real, l2_ua real,
  dip_p int, dip_ua_p int, peace_p int, built_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cutoff_hour, d))"""
COLS = ["posts_p", "posts_ru", "thr_ru", "l2_ru", "tgt_ru", "thr_ua", "l2_ua", "dip_p", "dip_ua_p", "peace_p"]


def lower(s: str) -> str:
    # Postgres lower() under the builtin C.UTF-8 provider uses simple case mapping (one char -> one char); Python's
    # str.lower() maps 'İ' to two chars. Everything else (Cyrillic, Latin) is identical.
    return s.replace("İ", "i").lower()


def rule_labels(text: str) -> dict:
    x = lower(text)
    r = {k: bool(re.search(p, x)) for k, p in RULES.items()}
    if r["r_operational"] and not r["r_threat"]:
        lvl = 0
    elif r["r_threat"] and r["r_target"] and r["r_time"]:
        lvl = 3
    elif r["r_threat"] and (r["r_target"] or r["r_time"]):
        lvl = 2
    else:
        lvl = 1 if r["r_threat"] else 0
    return {**r, "rule_threat_level": lvl}


def daily(posts: pd.DataFrame, cutoff_hour: int) -> pd.DataFrame:
    """Same windows and filters as features.rhetoric(), restricted to the panel (posts: source_id, ts_published UTC, text)."""
    p = posts[posts.text.str.len() >= 40].copy()
    ts = pd.to_datetime(p.ts_published, utc=True).dt.tz_convert("Europe/Kyiv").dt.tz_localize(None)
    p["d"] = (ts + pd.Timedelta(hours=24 - cutoff_hour)).dt.normalize() + pd.Timedelta(days=1)
    lab = pd.DataFrame([rule_labels(t) for t in p.text], index=p.index)
    p = p.join(lab)
    x = p.text.map(lower)
    p["l2"] = (p.rule_threat_level >= 2).astype(int)
    has = lambda pat: x.map(lambda s: re.search(pat, s) is not None)  # noqa: E731
    p["dip"] = has(ENVOY_RE)
    p["dip_ua"] = p.dip & ~has(IRAN_RE)
    p["peace"] = has(PEACE_RE)
    p["country"] = p.source_id.map(COUNTRY)
    ru, ua = p[p.country == "RU"], p[p.country == "UA"]
    out = pd.DataFrame({
        "posts_p": p.groupby("d").size(),
        "posts_ru": ru.groupby("d").size(),
        "thr_ru": ru.groupby("d").r_threat.mean(), "l2_ru": ru.groupby("d").l2.mean(), "tgt_ru": ru.groupby("d").r_target.mean(),
        "thr_ua": ua.groupby("d").r_threat.mean(), "l2_ua": ua.groupby("d").l2.mean(),
        "dip_p": p.groupby("d").dip.sum(), "dip_ua_p": p.groupby("d").dip_ua.sum(), "peace_p": p.groupby("d").peace.sum()})
    out.index.name = "d"
    return out.reset_index()


# ---------------------------------------------------------------- collect / pack
def cmd_collect(a):
    import collect_telegram as ct
    ct.OUT_DIR = HIST  # never data/telegram: load_to_db.py messages globs that folder into Neon
    HIST.mkdir(parents=True, exist_ok=True)
    f = HIST / f"{a.channel}.jsonl"
    since = datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc)
    last = None
    for chunk in range(a.chunks):
        rows = [json.loads(l) for l in f.open(encoding="utf-8")] if f.exists() and f.stat().st_size else []
        before = min((r["msg_num"] for r in rows), default=a.before)
        oldest = min((r["ts_published"] for r in rows if r.get("ts_published")), default=None)
        if oldest and oldest[:10] < a.since:
            print(f"{a.channel}: reached {oldest[:10]} ({len(rows)} posts)")
            return
        if before == last:  # no progress: t.me reset or the channel start
            print(f"{a.channel}: no progress below {before}, stopping ({len(rows)} posts, oldest {oldest})")
            return
        last = before
        print(f"{a.channel} chunk {chunk}: before {before}, oldest {oldest}", flush=True)
        ct.collect(a.channel, a.pages, since, a.delay, start_before=before)
        time.sleep(15)


def cmd_pack(a):
    frames = []
    for f in sorted(HIST.glob("*.jsonl")):
        rows = [json.loads(l) for l in f.open(encoding="utf-8")]
        frames.append(pd.DataFrame(rows)[["id", "source_id", "msg_num", "ts_published", "text", "url", "views"]])
    df = pd.concat(frames).drop_duplicates("id").sort_values(["source_id", "msg_num"])
    df = df[df.ts_published.notna()]
    df.to_parquet(PARQUET, index=False, compression="zstd")
    s = df.groupby("source_id").ts_published.agg(["min", "max", "count"])
    print(s.to_string())
    print(f"{len(df)} posts -> {PARQUET} ({PARQUET.stat().st_size / 1e6:.1f} MB)")


# ---------------------------------------------------------------- aggregate / verify
def connect():
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return psycopg.connect(os.environ["DATABASE_URL"])


def cmd_aggregate(a):
    posts = pd.read_parquet(PARQUET)
    out = []
    for c in CUTOFFS:
        d = daily(posts, c)
        d = d[d.d < pd.Timestamp(PANEL_START)]  # live SQL covers PANEL_START onwards
        d.insert(0, "cutoff_hour", c)
        out.append(d)
    df = pd.concat(out)
    print(df.groupby("cutoff_hour").d.agg(["min", "max", "count"]).to_string())
    print(df[df.cutoff_hour == 24][COLS].describe().round(3).to_string())
    if a.dry_run:
        return
    vals = [(int(r.cutoff_hour), r.d.date(), *[None if pd.isna(v) else (float(v) if isinstance(v, float) else int(v)) for v in (getattr(r, k) for k in COLS)])
            for r in df.itertuples()]
    with connect() as conn:
        conn.execute(DDL)
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO rhetoric_daily_hist(cutoff_hour, d, {','.join(COLS)}) VALUES ({','.join(['%s'] * (len(COLS) + 2))}) "
                            f"ON CONFLICT (cutoff_hour, d) DO UPDATE SET {','.join(f'{k}=EXCLUDED.{k}' for k in COLS)}, built_at=now()", vals)
        conn.commit()
    print(f"upserted {len(vals)} rows into rhetoric_daily_hist")


def cmd_verify(a):
    """Python rules over the live panel posts vs the SQL view, same windows: max abs difference per column."""
    from features import rhetoric
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT m.source_id, m.ts_published, m.text FROM messages m JOIN sources s ON s.id = m.source_id "
                        "WHERE m.source_id = ANY(%s) AND s.type <> 'osint' AND m.ts_published >= %s::date - 2", (PANEL, PANEL_START))
            posts = pd.DataFrame(cur.fetchall(), columns=["source_id", "ts_published", "text"])
        print(f"{len(posts)} live panel posts")
        worst = {}
        for c in CUTOFFS:
            sql = rhetoric(conn, c)
            py = daily(posts, c)
            j = sql.merge(py, on="d", how="inner", suffixes=("", "_py"))
            j = j[(j.d > pd.Timestamp(PANEL_START) + pd.Timedelta(days=1)) & (j.d < pd.Timestamp.now().normalize())]
            for k in ("posts_ru", "thr_ru", "l2_ru", "tgt_ru", "thr_ua", "l2_ua"):
                diff = (j[k].astype(float) - j[k + "_py" if k + "_py" in j else k].astype(float)).abs()
                worst[(c, k)] = float(diff.max())
            # panel-only counts have no SQL twin in rhetoric(); compare against an explicit SQL count
            with conn.cursor() as cur:
                cur.execute(f"""SELECT ((m.ts_published AT TIME ZONE 'Europe/Kyiv') + interval '{24 - c} hours')::date + 1 AS d,
                                  count(*) FILTER (WHERE lower(m.text) ~ %(e)s), count(*) FILTER (WHERE lower(m.text) ~ %(e)s AND lower(m.text) !~ %(i)s),
                                  count(*) FILTER (WHERE lower(m.text) ~ %(p)s), count(*)
                                FROM messages m WHERE m.source_id = ANY(%(panel)s) AND length(m.text) >= 40 AND m.ts_published >= %(s)s::date - 2 GROUP BY 1""",
                            {"e": ENVOY_RE, "i": IRAN_RE, "p": PEACE_RE, "panel": PANEL, "s": PANEL_START})
                cnt = pd.DataFrame(cur.fetchall(), columns=["d", "dip_p", "dip_ua_p", "peace_p", "posts_p"])
            cnt["d"] = pd.to_datetime(cnt.d)
            jj = cnt.merge(py, on="d", suffixes=("", "_py"))
            for k in ("dip_p", "dip_ua_p", "peace_p", "posts_p"):
                worst[(c, k)] = float((jj[k] - jj[k + "_py"]).abs().max())
            print(f"cutoff {c}: {len(j)} days compared")
        for (c, k), v in worst.items():
            print(f"  cutoff {c:2d} {k:9s} max |sql - python| = {v:.6f}")
        print("MAX", max(worst.values()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--channel", required=True)
    c.add_argument("--before", type=int, required=True, help="start below this message id (the live range's first id)")
    c.add_argument("--since", default="2025-01-01")
    c.add_argument("--pages", type=int, default=300)
    c.add_argument("--chunks", type=int, default=40)
    c.add_argument("--delay", type=float, default=0.7)
    c.set_defaults(fn=cmd_collect)
    sub.add_parser("pack").set_defaults(fn=cmd_pack)
    g = sub.add_parser("aggregate")
    g.add_argument("--dry-run", action="store_true")
    g.set_defaults(fn=cmd_aggregate)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    a = ap.parse_args()
    a.fn(a)
