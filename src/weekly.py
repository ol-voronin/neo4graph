"""Weekly oblast test of the rhetoric hypothesis, pre-registered in docs/weekly_test.md (read it before changing anything).

  python src/weekly.py [--controls 50] [--target esc|rear]     # needs DATABASE_URL; writes data/weekly_test.json

Rows are (oblast, ACLED week W). ACLED features start at W-2 (publication lag), rhetoric is the week W-1.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from acled import STRIKE_SUB
from features import connect
from nowcast import fit_predict_rows, metrics

ROOT = Path(__file__).resolve().parent.parent
START = "2022-03-05"
RHET = ["thr_ru", "l2_ru", "geo_ru", "geo_thr_ru", "geo_tcg_ru"]
MIN_TRAIN_WEEKS = 26
OCCUPIED = {"luhanska", "donetska"}


def load(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT region_id, week, sum(events) FILTER (WHERE sub_event_type = ANY(%s))::int FROM acled_weekly
                       WHERE country = 'Ukraine' AND region_id IS NOT NULL AND region_id <> 'crimea' AND week >= %s::date - 70 GROUP BY 1, 2""",
                    (list(STRIKE_SUB), START))
        aw = pd.DataFrame(cur.fetchall(), columns=["region_id", "week", "ev"])
        cur.execute("SELECT week, region_id, " + ", ".join(RHET) + " FROM rhetoric_weekly")
        rw = pd.DataFrame(cur.fetchall(), columns=["week", "region_id"] + RHET)
    for f in (aw, rw):
        f["week"] = pd.to_datetime(f["week"])
    return aw, rw


def build(aw: pd.DataFrame, rw: pd.DataFrame) -> pd.DataFrame:
    """Zero-filled (region, week) grid with targets and every feature column (pure, no DB).
    >>> aw = pd.DataFrame({"region_id": ["a"] * 12, "week": pd.date_range("2026-01-03", periods=12, freq="7D"), "ev": [1] * 10 + [5, 0]})
    >>> rw = pd.DataFrame({"week": [], "region_id": []})
    >>> b = build(aw, rw); b[["ev", "esc", "rear"]].tail(2).values.tolist()
    [[5.0, 1.0, nan], [0.0, 0.0, nan]]
    """
    grid = pd.MultiIndex.from_product([sorted(aw.region_id.unique()), pd.date_range(aw.week.min(), aw.week.max(), freq="7D")],
                                      names=["region_id", "week"]).to_frame(index=False)
    df = grid.merge(aw, on=["region_id", "week"], how="left").fillna({"ev": 0}).sort_values(["region_id", "week"]).reset_index(drop=True)
    df["ev"] = df["ev"].astype(float)
    g = df.groupby("region_id")["ev"]
    m8 = g.transform(lambda s: s.shift(1).rolling(8, min_periods=8).mean())
    df["esc"] = np.where(m8.isna(), np.nan, (df.ev >= np.maximum(3, 1.5 * m8)).astype(float))
    df["rear"] = np.where(m8.isna() | (m8 >= 1), np.nan, (df.ev >= 1).astype(float))
    le = np.log1p(df.ev)
    lg = le.groupby(df.region_id)
    df["ev_2"] = lg.shift(2)
    df["ev_m4"] = lg.transform(lambda s: s.shift(2).rolling(4, min_periods=4).mean())
    df["ev_m8"] = lg.transform(lambda s: s.shift(2).rolling(8, min_periods=8).mean())
    nat = np.log1p(df.groupby("week").ev.sum()).shift(2)
    df["nat_2"] = df.week.map(nat)
    df = df.merge(rw, on=["region_id", "week"], how="left")
    for c in RHET:
        if c not in df:
            df[c] = np.nan
        base = df.groupby("region_id")[c].transform(lambda s: s.shift(1).rolling(4, min_periods=3).mean())
        df[c + "_d"] = df[c] - base
    return df


def design(df, candidate, shift=0):
    F = pd.DataFrame(index=df.index)
    for r in sorted(df.region_id.unique()):
        F["reg_" + r] = (df.region_id == r).astype(float)
    for c in ("ev_2", "ev_m4", "ev_m8", "nat_2"):
        F[c] = df[c]
    if candidate:
        for c in RHET + [x + "_d" for x in RHET]:
            v = df[c]
            if shift:  # circular shift of the rhetoric over weeks (same for every region): keeps its autocorrelation
                weeks = np.sort(df.week.unique())
                mp = dict(zip(weeks, np.roll(weeks, shift)))
                src = df.set_index(["region_id", "week"])[c]
                v = pd.Series(src.reindex(list(zip(df.region_id, df.week.map(mp)))).to_numpy(), index=df.index)
            F[c] = v
    return F


def backtest(df, target, candidate, shift=0):
    F, y = design(df, candidate, shift), df[target]
    weeks = np.sort(df.week[df.week >= pd.Timestamp(START)].unique())
    out = []
    for i, w in enumerate(weeks):
        if i < MIN_TRAIN_WEEKS:
            continue
        rows = [r for r in df.index[(df.week == w) & y.notna()] if not F.loc[r].isna().any()]
        if not rows:
            continue
        p, _ = fit_predict_rows(F, y, (df.week < w) & y.notna(), rows, 1.0)
        if p is not None:
            out += [(w, df.region_id[r], int(y[r]), float(pr)) for r, pr in zip(rows, p)]
    return pd.DataFrame(out, columns=["week", "region_id", "y", "p"])


def block_ci(j, B=1000, L=4, seed=7):
    weeks = np.sort(j.week.unique())
    rng = np.random.default_rng(seed)
    by = {w: g for w, g in j.groupby("week")}
    deltas = []
    for _ in range(B):
        pick = []
        while len(pick) < len(weeks):
            s = rng.integers(0, max(1, len(weeks) - L + 1))
            pick += list(weeks[s:s + L])
        s = pd.concat([by[w] for w in pick[:len(weeks)]])
        a, b = metrics(s.y, s.p), metrics(s.y, s.p_b)
        if a["auc"] is not None and b["auc"] is not None:
            deltas.append(a["auc"] - b["auc"])
    return [round(float(np.percentile(deltas, 2.5)), 4), round(float(np.percentile(deltas, 97.5)), 4)]


def compare(c, b):
    j = c.merge(b, on=["week", "region_id", "y"], suffixes=("", "_b"))
    ma, mb = metrics(j.y, j.p, j.region_id), metrics(j.y, j.p_b, j.region_id)
    return j, ma, mb


def summary(j, ma, mb):
    return {"n": len(j), "pos": int(j.y.sum()), "auc_c": ma["auc"], "auc_b": mb["auc"], "within_c": ma.get("auc_within"), "within_b": mb.get("auc_within"),
            "brier_c": ma["brier"], "brier_b": mb["brier"], "delta": None if ma["auc"] is None or mb["auc"] is None else ma["auc"] - mb["auc"]}


def main(a):
    with connect() as conn:
        aw, rw = load(conn)
    df = build(aw, rw)
    df = df[df.week >= pd.Timestamp(START) - pd.Timedelta(weeks=MIN_TRAIN_WEEKS)].reset_index(drop=True)
    have = rw.week.min() if len(rw) else None
    print(f"rows {len(df)}, weeks {df.week.min().date()} .. {df.week.max().date()}, rhetoric from {have}")
    # rows without rhetoric cannot be scored by the candidate: both models are compared on the rhetoric-covered weeks
    df = df[df.week >= have].reset_index(drop=True) if have is not None else df
    b, c = backtest(df, a.target, False), backtest(df, a.target, True)
    j, ma, mb = compare(c, b)
    rep = {"target": a.target, "all": summary(j, ma, mb)}
    rep["all"]["ci"] = block_ci(j)
    for name, mask in (("pre_panel", j.week < pd.Timestamp("2026-03-28")), ("live_panel", j.week >= pd.Timestamp("2026-03-28")),
                       ("no_occupied", ~j.region_id.isin(OCCUPIED))):
        s = j[mask]
        if s.y.nunique() == 2:
            rep[name] = summary(s, metrics(s.y, s.p, s.region_id), metrics(s.y, s.p_b, s.region_id))
    if a.controls:
        rng = np.random.default_rng(11)
        n_w = df.week.nunique()
        null = []
        for k in rng.integers(4, max(5, n_w - 4), a.controls):
            jj, xa, xb = compare(backtest(df, a.target, True, int(k)), b)
            if xa["auc"] is not None and xb["auc"] is not None:
                null.append(xa["auc"] - xb["auc"])
        rep["control_mean"], rep["control_p"] = float(np.mean(null)), (1 + sum(x >= rep["all"]["delta"] for x in null)) / (1 + len(null))
    for k, v in rep.items():
        print(k, json.dumps(v, default=lambda x: round(x, 4) if isinstance(x, float) else str(x)) if isinstance(v, dict) else v)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / f"weekly_test_{a.target}.json").write_text(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="esc", choices=["esc", "rear"])
    ap.add_argument("--controls", type=int, default=0)
    main(ap.parse_args())
