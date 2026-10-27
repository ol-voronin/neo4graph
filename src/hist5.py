"""History aggregates for the oblast model v5 (docs/forecast_v5.md, migration 038): the panel and monitor Parquets ->
rhetoric_daily_hist5 / rhetoric_region_hist5 (12-channel panel, 2022-02..2026-03-31) and monitor_daily_hist5 /
monitor_region_hist5 (4 monitors, 2022-03..2026-10-23), for cutoffs 15 and 21. The live range is computed from `messages`
by features.py with the same rules (history.rule_labels = the SQL rule views; monitors.daily for both).

  python src/hist5.py build [--set panel|monitors] [--dry-run]
  python src/hist5.py verify         # overlap days: Parquet aggregates vs the live SQL (features.rhetoric / rhetoric_geo)
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from features import INFRA_RE, TC_RE  # noqa: E402
from history import COUNTRY, PARQUET, SETS, lower, rule_labels  # noqa: E402
from monitors import HIST_END, NAT, daily as monitor_daily  # noqa: E402

CUTOFFS = (15, 21)
# last target day whose windows lie inside panel_posts.parquet (ends 2026-03-31 22:15 UTC)
PANEL_HIST_END = pd.Timestamp("2026-04-01")
NAT_COLS = ["posts_ru", "thr_ru", "l2_ru", "tgt_ru", "tc_energy_ru", "tc_dc_ru", "tc_rail_ru", "tc_vpk_ru"]


def pg_re(p: str) -> re.Pattern:
    """Postgres ARE word boundaries \\m \\M -> Python \\b."""
    return re.compile(p.replace("\\m", "\\b").replace("\\M", "\\b"))


def panel_flags(posts: pd.DataFrame) -> pd.DataFrame:
    """Per RU panel post (>= 40 chars): thr, l2, tgt, tc_<class>, infra, and one column per region (names a place there).
    Cutoff-independent, so computed once."""
    from build_gazetteer import region_patterns, sql_regex
    p = posts[(posts.text.str.len() >= 40) & (posts.source_id.map(COUNTRY) == "RU")].copy()
    lab = pd.DataFrame([rule_labels(t) for t in p.text], index=p.index)
    x = p.text.map(lambda s: lower(s).replace("’", "'").replace("ʼ", "'").replace("`", "'").replace("‘", "'"))
    f = pd.DataFrame({"ts_published": p.ts_published, "thr": lab.r_threat.astype(int), "l2": (lab.rule_threat_level >= 2).astype(int),
                      "tgt": lab.r_target.astype(int)}, index=p.index)
    for k, v in TC_RE.items():
        rx = pg_re(v)
        f["tc_" + k] = (f.thr.astype(bool) & x.map(lambda s: rx.search(s) is not None)).astype(int)
    infra = pg_re(INFRA_RE)
    f["infra"] = x.map(lambda s: infra.search(s) is not None).astype(int)
    for r, alts in region_patterns().items():
        rx = re.compile(sql_regex(alts))
        f["g_" + r] = x.map(lambda s: rx.search(s) is not None).astype(int)
    return f


def panel_daily(f: pd.DataFrame, cutoff_hour: int, regions) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flags -> (nat: d + NAT_COLS, reg: d, region_id, n_thr_geo_ru, n_tcg_ru) with the window of features.rhetoric().
    >>> f = pd.DataFrame({"ts_published": pd.to_datetime(["2026-03-01 10:00", "2026-03-01 12:00"], utc=True), "thr": [1, 0], "l2": [1, 0], "tgt": [1, 0],
    ...     "tc_energy": [1, 0], "tc_dc": [0, 0], "tc_rail": [0, 0], "tc_vpk": [0, 0], "infra": [1, 0], "g_kharkivska": [1, 0], "g_lvivska": [0, 1]})
    >>> n, r = panel_daily(f, 15, ["kharkivska", "lvivska"])
    >>> n[["d", "posts_ru", "thr_ru", "tc_energy_ru"]].values.tolist()
    [[Timestamp('2026-03-02 00:00:00'), 2, 0.5, 0.5]]
    >>> r[["region_id", "n_thr_geo_ru", "n_tcg_ru"]].values.tolist()
    [['kharkivska', 1, 1], ['lvivska', 0, 0]]
    """
    from monitors import window_day
    d = window_day(f.ts_published, cutoff_hour)
    nat = pd.DataFrame({"posts_ru": f.groupby(d).size(), "thr_ru": f.thr.groupby(d).mean(), "l2_ru": f.l2.groupby(d).mean(),
                        "tgt_ru": f.tgt.groupby(d).mean(), **{"tc_%s_ru" % k: f["tc_" + k].groupby(d).mean() for k in TC_RE}})
    nat = nat.rename_axis("d").reset_index()
    rows = []
    for r in sorted(regions):
        g = f.get("g_" + r, pd.Series(0, index=f.index))
        a = (f.thr * g).groupby(d).sum()
        b = (f.thr * g * f.infra).groupby(d).sum()
        rows.append(pd.DataFrame({"d": a.index, "region_id": r, "n_thr_geo_ru": a.to_numpy(int), "n_tcg_ru": b.reindex(a.index).to_numpy(int)}))
    return nat[["d"] + NAT_COLS], pd.concat(rows, ignore_index=True)


def write(conn, table, cutoff, df, cols, dry):
    if dry:
        print(f"{table} cutoff {cutoff}: {len(df)} rows, {df.d.min().date()}..{df.d.max().date()}")
        return
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE cutoff_hour = %s", (cutoff,))
        with cur.copy(f"COPY {table} (cutoff_hour, d, {', '.join(cols)}) FROM STDIN") as cp:
            for r in df.itertuples(index=False):
                vals = [cutoff, r.d.date()] + [getattr(r, c) for c in cols]
                cp.write_row([None if (isinstance(v, float) and np.isnan(v)) else (int(v) if isinstance(v, (np.integer,)) else v) for v in vals])
        print(f"{table} cutoff {cutoff}: {cur.rowcount} rows")
    conn.commit()


def cmd_build(a):
    from load_to_db import connect
    from oblasts import PANEL_REGIONS
    regions = PANEL_REGIONS
    global CUTOFFS
    CUTOFFS = tuple(int(x) for x in a.cutoffs.split(","))
    with connect() as conn:
        if a.set in ("panel", "all"):
            posts = pd.read_parquet(PARQUET, columns=["source_id", "ts_published", "text"])
            posts["ts_published"] = pd.to_datetime(posts.ts_published, utc=True)
            print(f"panel posts {len(posts)}; flags...", flush=True)
            f = panel_flags(posts)
            print(f"RU panel posts >= 40 chars: {len(f)}", flush=True)
            for c in CUTOFFS:
                nat, reg = panel_daily(f, c, regions)
                nat, reg = nat[nat.d <= PANEL_HIST_END], reg[reg.d <= PANEL_HIST_END]
                write(conn, "rhetoric_daily_hist5", c, nat, NAT_COLS, a.dry_run)
                write(conn, "rhetoric_region_hist5", c, reg, ["region_id", "n_thr_geo_ru", "n_tcg_ru"], a.dry_run)
        if a.set in ("monitors", "all"):
            _, parquet = SETS["monitors"]
            posts = pd.read_parquet(parquet, columns=["id", "ts_published", "text"])
            posts["ts_published"] = pd.to_datetime(posts.ts_published, utc=True)
            print(f"monitor posts {len(posts)}", flush=True)
            for c in CUTOFFS:
                nat, reg = monitor_daily(posts, c, regions)
                nat, reg = nat[nat.d <= HIST_END], reg[reg.d <= HIST_END]
                write(conn, "monitor_daily_hist5", c, nat, NAT, a.dry_run)
                write(conn, "monitor_region_hist5", c, reg, ["region_id", "mon_reg"], a.dry_run)


def cmd_verify(a):
    """Parquet aggregates vs the live code path where both exist: the rhetoric overlap is at most the last two days of
    March 2026 (the live panel starts 2026-03-31); the monitors overlap 2026-10-16..10-23 (live monitor posts vs Parquet)."""
    from features import rhetoric
    from load_to_db import connect
    from monitors import HIST_END, daily, live_posts
    from oblasts import PANEL_REGIONS
    with connect() as conn:
        for c in CUTOFFS:
            h = _rows(conn, f"SELECT d, {', '.join(NAT_COLS)} FROM rhetoric_daily_hist5 WHERE cutoff_hour = {c}", ["d"] + NAT_COLS)
            j = h.merge(rhetoric(conn, c), on="d", suffixes=("_h", ""))
            if j.empty:
                print(f"cutoff {c}: rhetoric: no overlap day (expected: the live panel starts where the Parquet ends)")
            else:
                print(f"cutoff {c}: rhetoric overlap {len(j)} days; max |diff|: " + ", ".join(f"{k} {np.nanmax(np.abs(j[k + '_h'] - j[k])):.3f}" for k in NAT_COLS))
            hm = _rows(conn, f"SELECT d, {', '.join(NAT)} FROM monitor_daily_hist5 WHERE cutoff_hour = {c}", ["d"] + NAT)
            ln, lr = daily(live_posts(conn, "2026-10-14"), c, PANEL_REGIONS)
            j = hm.merge(ln[(ln.d >= "2026-10-17") & (ln.d <= HIST_END)], on="d", suffixes=("_h", ""))
            print(f"  monitors overlap {len(j)} days; Parquet vs live: " + ", ".join(f"{k} {int(j[k + '_h'].sum())}/{int(j[k].sum())}" for k in NAT))
            hr = _rows(conn, f"SELECT d, region_id, mon_reg FROM monitor_region_hist5 WHERE cutoff_hour = {c}", ["d", "region_id", "mon_reg"])
            jr = hr.merge(lr[(lr.d >= "2026-10-17") & (lr.d <= HIST_END)], on=["d", "region_id"], suffixes=("_h", ""))
            print(f"  mon_reg: {int(jr.mon_reg_h.sum())}/{int(jr.mon_reg.sum())} reports, max |diff| per region-day {int((jr.mon_reg_h - jr.mon_reg).abs().max())}")


def _rows(conn, sql, cols):
    with conn.cursor() as cur:
        cur.execute(sql)
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    df["d"] = pd.to_datetime(df.d)
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--set", choices=["panel", "monitors", "all"], default="all")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--cutoffs", default="15,21", help="cutoff hours to build (e.g. 23 for an exploratory late window)")
    b.set_defaults(fn=cmd_build)
    v = sub.add_parser("verify")
    v.set_defaults(fn=cmd_verify)
    a = ap.parse_args()
    a.fn(a)
