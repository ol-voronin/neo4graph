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
# Target classes named in threat posts (model ua_oblast_massive2_*): what the threat is about, not where
TC_RE = {
    "energy": r"(енерг|энерг|тец|тэц|тес\M|тэс\M|підстанц|подстанц|електростанц|электростанц|енергосистем|энергосистем|укренерго|укрэнерго)",
    "dc": r"(дата-?центр|датацентр|\mцод|центр\w* обробки даних|центр\w* обработки данных|серверн)",
    "rail": r"(залізн|железнодор|депо\M|локомотив|укрзалізн|укрзализн|тягов\w* підстанц|тягов\w* подстанц)",
    "vpk": r"(\mвпк\M|оборонн\w* (підприєм|предприят|завод)|військов\w* завод|военн\w* завод|завод)",
}
INFRA_RE = "(" + "|".join(TC_RE[k][1:-1] for k in ("energy", "dc", "rail")) + ")"
# Oblasts where the absence of an air-raid alert says nothing (occupied: alerts do not sound there)
NO_ALERT_NEG = {"luhanska", "crimea"}


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
      count(*) FILTER (WHERE x ~ %(peace)s) AS peace,
      avg((r_threat AND x ~ %(tc_energy)s)::int) FILTER (WHERE panel AND country = 'RU')::float AS tc_energy_ru,
      avg((r_threat AND x ~ %(tc_dc)s)::int) FILTER (WHERE panel AND country = 'RU')::float AS tc_dc_ru,
      avg((r_threat AND x ~ %(tc_rail)s)::int) FILTER (WHERE panel AND country = 'RU')::float AS tc_rail_ru,
      avg((r_threat AND x ~ %(tc_vpk)s)::int) FILTER (WHERE panel AND country = 'RU')::float AS tc_vpk_ru
    FROM m GROUP BY d ORDER BY d"""
    with conn.cursor() as cur:
        cur.execute(q, {"panel": PANEL, "start": PANEL_START, "envoy": ENVOY_RE, "iran": IRAN_RE, "peace": PEACE_RE,
                        **{"tc_" + k: v for k, v in TC_RE.items()}})
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


# ---------------------------------------------------------------------------------------------------------------------
# Oblast panel (long format): one row per (region_id, d). Same real-time rule as above, applied per region.
# Ground truth: view oblast_daily_gt (migration 015). Region-specific rhetoric: geo_regions() (migration 016).
# ---------------------------------------------------------------------------------------------------------------------

def oblast_gt(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("""SELECT region_id, date, missiles, drones, kab, attacked, complete, ee_reports, alert_n, alert_min
                       FROM oblast_daily_gt WHERE date >= %s::date - 30""", (PANEL_START,))
        gt = pd.DataFrame(cur.fetchall(), columns=["region_id", "d", "missiles", "drones", "kab", "attacked", "complete", "ee_reports", "alert_n", "alert_min"])
        # n_alert_regions: oblasts with an air-raid alert that day; >= 3 means the alert feed was alive (a day with no
        # rows at all is a data gap, not a quiet day) -- used for the honest negatives of ob_massive2
        cur.execute("""SELECT coalesce(e.date, a.date), e.n_extracted, a.n FROM (SELECT date, n_extracted FROM extraction_coverage_daily
                       WHERE date >= %s::date - 30) e FULL JOIN (SELECT date, count(*)::int n FROM oblast_alerts_daily WHERE date >= %s::date - 30
                       GROUP BY 1) a ON a.date = e.date""", (PANEL_START, PANEL_START))
        cov = pd.DataFrame(cur.fetchall(), columns=["d", "n_extracted", "n_alert_regions"])
    for f in (gt, cov):
        f["d"] = pd.to_datetime(f["d"])
    return gt, cov


def rhetoric_geo(conn, cutoff_hour: int) -> pd.DataFrame:
    """Per target day D and region: RU panel posts flagged r_threat that name a place in the region (geo_regions()),
    same 24-hour window as rhetoric(). Empty frame (-> NaN -> no forecast) if migration 016 is not applied."""
    shift = f"{24 - cutoff_hour} hours"
    q = f"""
    SELECT ((m.ts_published AT TIME ZONE 'Europe/Kyiv') + interval '{shift}')::date + 1 AS d, g.region_id, count(*) AS n_thr_geo_ru,
           count(*) FILTER (WHERE lower(m.text) ~ %(infra)s) AS n_tcg_ru
    FROM messages m JOIN sources s ON s.id = m.source_id JOIN rule_labels r ON r.message_id = m.id
    CROSS JOIN LATERAL unnest(geo_regions(m.text)) AS g(region_id)
    WHERE m.ts_published >= %(start)s::date - 2 AND s.id = ANY(%(panel)s) AND s.country = 'RU' AND length(m.text) >= 40 AND r.r_threat
    GROUP BY 1, 2"""
    try:
        with conn.cursor() as cur:
            cur.execute(q, {"panel": PANEL, "start": PANEL_START, "infra": INFRA_RE})
            df = pd.DataFrame(cur.fetchall(), columns=["d", "region_id", "n_thr_geo_ru", "n_tcg_ru"])
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"rhetoric_geo unavailable ({str(e).splitlines()[0][:120]}): region signals will be missing")
        df = pd.DataFrame(columns=["d", "region_id", "n_thr_geo_ru", "n_tcg_ru"])
    df["d"] = pd.to_datetime(df["d"])
    return df


def label_oblast(gt: pd.DataFrame, cov: pd.DataFrame, ee_min_extracted: int) -> pd.DataFrame:
    """Targets per (region_id, d). Positive evidence from any source; a NEGATIVE needs a complete OVA summary that day
    or an events extractor that read >= ee_min_extracted posts of that day. Otherwise NaN (unknown, not zero).
      ob_massive      owner's rule at oblast level: missiles >= 7 or strike drones >= 20 (FPV excluded)
      ob_missile_day  missiles > 0 (the Kharkiv candidate target, now per oblast)
    >>> gt = pd.DataFrame({"region_id": ["a", "a", "b", "b"], "d": pd.to_datetime(["2026-05-01", "2026-05-02"] * 2),
    ...                    "missiles": [8, 0, None, None], "drones": [0, 3, None, 25], "complete": [True, True, False, False]})
    >>> cov = pd.DataFrame({"d": pd.to_datetime(["2026-05-01", "2026-05-02"]), "n_extracted": [10, 500]})
    >>> label_oblast(gt, cov, 100)[["region_id", "ob_massive", "ob_missile_day"]].values.tolist()
    [['a', 1.0, 1.0], ['a', 0.0, 0.0], ['b', nan, nan], ['b', 1.0, 0.0]]
    """
    t = gt.merge(cov, on="d", how="left")
    mis, dr = t["missiles"].fillna(0), t["drones"].fillna(0)
    known_neg = t["complete"].fillna(False).astype(bool) | (t["n_extracted"].fillna(0) >= ee_min_extracted)
    for name, hit in (("ob_massive", (mis >= 7) | (dr >= 20)), ("ob_missile_day", mis > 0)):
        t[name] = np.where(hit, 1.0, np.where(known_neg, 0.0, np.nan))
    return t.drop(columns=[c for c in ("n_extracted", "n_alert_regions") if c in t])


def label_oblast2(df: pd.DataFrame, cov: pd.DataFrame) -> pd.DataFrame:
    """ob_massive2 on the regions x days grid (model ua_oblast_massive2_*): same positive rule as ob_massive, honest
    negatives. A day is a known NEGATIVE for an oblast when a complete OVA summary says so, or when the oblast had no
    oblast-level air-raid alert that Kyiv day while the alert feed was alive (>= 3 oblasts alerted that day): a massed
    strike (>= 7 missiles or >= 20 strike drones) without an alert did not occur outside occupied oblasts (checked on
    2026-03-31..10-21: no positive without an alert except Luhansk, where alerts do not sound). The LLM extractor's
    reading volume no longer makes negatives (it says nothing about a given oblast). Otherwise NaN (unknown).
    >>> g = pd.DataFrame({"region_id": ["a", "a", "a", "luhanska"], "d": pd.to_datetime(["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-02"]),
    ...                   "missiles": [8, None, None, None], "drones": [0, None, None, None], "complete": [False, None, None, None],
    ...                   "alert_n": [3, None, 2, None]})
    >>> cov = pd.DataFrame({"d": pd.to_datetime(["2026-05-01", "2026-05-02", "2026-05-03"]), "n_alert_regions": [10, 9, 1]})
    >>> label_oblast2(g, cov)["ob_massive2"].tolist()
    [1.0, 0.0, nan, nan]
    """
    alive = (cov.set_index("d")["n_alert_regions"].fillna(0) >= 3) if "n_alert_regions" in cov else pd.Series(dtype=bool)
    live = df["d"].map(alive).eq(True)
    mis, dr = df["missiles"].fillna(0), df["drones"].fillna(0)
    hit = (mis >= 7) | (dr >= 20)
    no_alert = df["alert_n"].isna() if "alert_n" in df else pd.Series(False, index=df.index)
    neg = df["complete"].fillna(False).astype(bool) | (live & no_alert & ~df["region_id"].isin(NO_ALERT_NEG))
    df = df.copy()
    df["ob_massive2"] = np.where(hit, 1.0, np.where(neg, 0.0, np.nan))
    return df


def build_long(nat: pd.DataFrame, geo: pd.DataFrame, gt: pd.DataFrame, cov: pd.DataFrame, start, end, regions,
               ee_min_extracted: int = 100) -> pd.DataFrame:
    """Pure assembly (no DB): regions x days grid with national rhetoric (per d), regional rhetoric (per region, d),
    ground truth and targets. Rows sorted by (region_id, d) with a fresh RangeIndex."""
    days = pd.date_range(start, end, freq="D")
    grid = pd.MultiIndex.from_product([sorted(regions), days], names=["region_id", "d"]).to_frame(index=False)
    lab = label_oblast(gt, cov, ee_min_extracted) if len(gt) else gt.assign(ob_massive=np.nan, ob_missile_day=np.nan)
    df = grid.merge(lab, on=["region_id", "d"], how="left").merge(nat, on="d", how="left")
    df = label_oblast2(df, cov)
    df = df.merge(geo, on=["region_id", "d"], how="left")
    have_geo = df.d.isin(set(geo.d)) if len(geo) else pd.Series(False, index=df.index)
    # a region with no threat post naming it on a day the query ran has 0, not NaN; days outside the query stay NaN
    for c in ("n_thr_geo_ru", "n_tcg_ru"):
        if c not in df:
            df[c] = np.nan
        df[c] = df[c].where(df[c].notna() | ~have_geo, 0)
    if "posts_ru" in df:
        df["thr_geo_ru"] = df["n_thr_geo_ru"] / df["posts_ru"].where(df["posts_ru"] > 0)
        df["tcg_ru"] = df["n_tcg_ru"] / df["posts_ru"].where(df["posts_ru"] > 0)
    return df.sort_values(["region_id", "d"]).reset_index(drop=True)


def frame_long(conn, cutoff_hour: int, until=None, regions=None, ee_min_extracted: int = 100) -> pd.DataFrame:
    from oblasts import PANEL_REGIONS
    end = pd.Timestamp(until) if until is not None else pd.Timestamp.now(tz="Europe/Kyiv").tz_localize(None).normalize() + pd.Timedelta(days=1)
    nat = rhetoric(conn, cutoff_hour)
    gt, cov = oblast_gt(conn)
    return build_long(nat, rhetoric_geo(conn, cutoff_hour), gt, cov, PANEL_START, end, regions or PANEL_REGIONS, ee_min_extracted)


def design_long(df: pd.DataFrame, spec: dict, signal_shift: int = 0) -> pd.DataFrame:
    """Pooled-model inputs per (region, D), only information available before D's outcome window (see module doc):
      reg_<slug>   region indicator (region as a feature)
      base         region base rate: expanding mean of the region's target up to D-2, shrunk toward the all-region
                   rate with weight spec["shrink"] (pseudo-days), so sparse regions borrow strength
      y2, y2_m7    own target at D-2 and its 7-day mean (unknown days filled with the base rate, y2_known flags them)
      <exposure>   e.g. alert_min: log1p of D-2 and 7-day mean (alerts are a threat-presence proxy, never the label)
      <signal>_1, _m3        national signals, window ending at the cutoff on D-1 (as design())
      <rsignal>_1, _m3       region signals, same window, per region
    signal_shift: circularly shift every signal column by this many days (shuffled control; keeps autocorrelation).
    """
    y = df[spec["target"]]
    g = df.groupby("region_id", sort=False)
    F = pd.DataFrame(index=df.index)
    for r in sorted(df.region_id.unique()):
        F["reg_" + r] = (df.region_id == r).astype(float)
    y2 = g[spec["target"]].shift(2)
    m = spec.get("shrink", 20)
    known = y2.notna().astype(float)
    r_sum = y2.fillna(0).groupby(df.region_id).cumsum()
    r_n = known.groupby(df.region_id).cumsum()
    day_sum = y2.fillna(0).groupby(df.d).transform("sum")
    day_n = known.groupby(df.d).transform("sum")
    order = df.d.rank(method="dense")
    tot = pd.DataFrame({"o": order, "s": day_sum, "n": day_n}).drop_duplicates("o").set_index("o").sort_index().cumsum()
    g_rate = (tot.s / tot.n.where(tot.n > 0)).reindex(order.values).to_numpy()
    g_rate = np.where(np.isnan(g_rate), 0.5, g_rate)
    F["base"] = (r_sum + m * g_rate) / (r_n + m)
    F["y2"] = y2.fillna(F["base"])
    F["y2_known"] = known
    F["y2_m7"] = y2.groupby(df.region_id).transform(lambda s: s.rolling(7, min_periods=1).mean()).fillna(F["base"])
    for e in spec.get("exposure", []):
        lag = np.log1p(g[e].shift(2))
        F[e + "_2"] = lag
        F[e + "_m7"] = lag.groupby(df.region_id).transform(lambda s: s.rolling(7, min_periods=4).mean())

    def shifted(col):
        s = df[col]
        if not signal_shift:
            return s
        days = np.sort(df.d.unique())
        mp = dict(zip(days, np.roll(days, signal_shift)))
        src = df.set_index(["region_id", "d"])[col]
        return pd.Series(src.reindex(list(zip(df.region_id, df.d.map(mp)))).to_numpy(), index=df.index)

    for s in spec.get("signals", []) + spec.get("region_signals", []):
        v = shifted(s)
        F[s + "_1"] = v
        F[s + "_m3"] = v.groupby(df.region_id).transform(lambda x: x.rolling(3, min_periods=2).mean())
    return F


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", type=int, default=24)
    ap.add_argument("--tail", type=int, default=10)
    ap.add_argument("--long", action="store_true", help="oblast panel (needs migrations 015/016)")
    a = ap.parse_args()
    with connect() as c:
        print((frame_long(c, a.cutoff) if a.long else frame(c, a.cutoff)).tail(a.tail).to_string())
