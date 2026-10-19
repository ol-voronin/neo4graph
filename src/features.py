"""Daily feature frame for the prospective nowcast (docs/architecture.md, 6.6). One code path for backtest and live use.

Real-time rule: a forecast for target day D is issued before D's outcome window opens, and every feature must already
exist at that moment.
  - Rhetoric features for D come from a 24-hour window that ENDS at `cutoff_hour` (Kyiv) on D-1:
    posts with local time in [D-2 cutoff, D-1 cutoff). cutoff_hour=24 is the plain calendar day D-1 (tests 2–4).
  - Own-target lags start at D-2: the Kharkiv governor's summary for D-1 appears on the morning of D, and the Air Force
    day summary for D-1 appears in the evening of D-1, both after the issue time.

  python src/features.py --cutoff 15 --tail 5        # print the last rows (needs DATABASE_URL)
"""
import argparse
import os

import numpy as np
import pandas as pd

# 12-channel rhetoric panel of tests 1–4 (same list as label_messages.PANEL)
PANEL = ["tg:MID_Russia", "tg:rybar", "tg:dva_majors", "tg:boris_rozhin", "tg:voenkorKotenok", "tg:sashakots", "tg:wargonzo",
         "tg:RVvoenkor", "tg:meduzalive", "tg:V_Zelenskiy_official", "tg:GeneralStaffZSU", "tg:ukrpravda_news"]
ENVOY_RE = r"(віткоф|уиткоф|уіткоф|witkoff|кушнер|kushner)"
IRAN_RE = r"(іран|иран|iran|пакистан|pakistan|ісламабад|исламабад|islamabad)"
PEACE_RE = r"(переговор|перемир|мирн\w* (угод|план|процес|соглаш)|peace (talks|deal|plan)|ceasefire)"
PANEL_START = "2026-03-31"


def connect():
    import psycopg
    from dotenv import load_dotenv
    load_dotenv()
    return psycopg.connect(os.environ["DATABASE_URL"])


def rhetoric(conn, cutoff_hour: int) -> pd.DataFrame:
    """Per target day D: panel rule shares by side, envoy / peace mention counts over the whole corpus."""
    shift = f"{24 - cutoff_hour} hours"
    q = f"""
    WITH m AS (
      SELECT ((m.ts_published AT TIME ZONE 'Europe/Kyiv') + interval '{shift}')::date + 1 AS d,
             s.country, s.id = ANY(%(panel)s) AS panel, lower(m.text) AS x, r.r_threat, r.r_target, r.rule_threat_level
      FROM messages m JOIN sources s ON s.id = m.source_id JOIN rule_labels r ON r.message_id = m.id
      WHERE m.ts_published >= %(start)s::date - 2 AND s.type <> 'osint' AND length(m.text) >= 40)
    SELECT d,
      count(*) AS posts,
      count(*) FILTER (WHERE panel AND country = 'RU') AS posts_ru,
      avg(r_threat::int) FILTER (WHERE panel AND country = 'RU')::float AS thr_ru,
      avg((rule_threat_level >= 2)::int) FILTER (WHERE panel AND country = 'RU')::float AS l2_ru,
      avg(r_target::int) FILTER (WHERE panel AND country = 'RU')::float AS tgt_ru,
      avg(r_threat::int) FILTER (WHERE panel AND country = 'UA')::float AS thr_ua,
      avg((rule_threat_level >= 2)::int) FILTER (WHERE panel AND country = 'UA')::float AS l2_ua,
      count(*) FILTER (WHERE x ~ %(envoy)s) AS dip,
      count(*) FILTER (WHERE x ~ %(envoy)s AND x !~ %(iran)s) AS dip_ua,
      count(*) FILTER (WHERE x ~ %(peace)s) AS peace
    FROM m GROUP BY d ORDER BY d"""
    with conn.cursor() as cur:
        cur.execute(q, {"panel": PANEL, "start": PANEL_START, "envoy": ENVOY_RE, "iran": IRAN_RE, "peace": PEACE_RE})
        cols = [c.name for c in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    df["d"] = pd.to_datetime(df["d"])
    return df


def targets(conn) -> pd.DataFrame:
    """Outcomes per calendar day. Waves are versioned: the latest version of each summary counts.
    national: Air Force night ("у ніч на D", from ~18:00 on D-1) + day summaries dated D.
    kharkiv: governor's daily summary covering D."""
    with conn.cursor() as cur:
        cur.execute("""
          WITH w AS (SELECT DISTINCT ON (id) id, date, launched_missiles, launched_drones FROM waves ORDER BY id, version DESC)
          SELECT date AS d, sum(coalesce(launched_missiles, 0))::int AS nat_missiles, sum(coalesce(launched_drones, 0))::int AS nat_drones
          FROM w GROUP BY date""")
        nat = pd.DataFrame(cur.fetchall(), columns=["d", "nat_missiles", "nat_drones"])
        cur.execute("SELECT date, missiles, drones, guided_bombs FROM kharkiv_daily")
        kh = pd.DataFrame(cur.fetchall(), columns=["d", "kh_missiles", "kh_drones", "kh_kab"])
    for f in (nat, kh):
        f["d"] = pd.to_datetime(f["d"])
    t = nat.merge(kh, on="d", how="outer").sort_values("d").reset_index(drop=True)
    t["nat_massive"] = np.where(t.nat_missiles.notna(), ((t.nat_missiles >= 20) | (t.nat_drones >= 400)).astype(float), np.nan)
    t["kh_missile_day"] = np.where(t.kh_missiles.notna(), (t.kh_missiles > 0).astype(float), np.nan)
    t["kh_idx"] = np.log1p(10 * t.kh_missiles.fillna(0) + t.kh_drones.fillna(0) + t.kh_kab.fillna(0)).where(t.kh_missiles.notna())
    return t


def frame(conn, cutoff_hour: int, until=None) -> pd.DataFrame:
    """Continuous daily index from the panel start to `until` (default: tomorrow), rhetoric + targets joined."""
    r, t = rhetoric(conn, cutoff_hour), targets(conn)
    end = pd.Timestamp(until) if until is not None else pd.Timestamp.now(tz="Europe/Kyiv").tz_localize(None).normalize() + pd.Timedelta(days=1)
    idx = pd.DataFrame({"d": pd.date_range(PANEL_START, end, freq="D")})
    return idx.merge(r, on="d", how="left").merge(t, on="d", how="left")


def design(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """Model inputs for each row D, using only information available before D's outcome window (see module doc)."""
    y = df[spec["target"]]
    F = pd.DataFrame(index=df.index)
    F["y2"] = y.shift(2)
    F["y2_m7"] = y.shift(2).rolling(7, min_periods=4).mean()
    for s in spec.get("signals", []):
        F[s + "_1"] = df[s]                       # already the window that ends at the cutoff on D-1
        F[s + "_m3"] = df[s].rolling(3, min_periods=2).mean()
    return F


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", type=int, default=24)
    ap.add_argument("--tail", type=int, default=10)
    a = ap.parse_args()
    with connect() as c:
        print(frame(c, a.cutoff).tail(a.tail).to_string())
