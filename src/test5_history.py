"""Test 5: tests 2 and 4 re-run on the long history (2025-01-01 onwards, src/history.py), with the live feature timing.

Pre-registered before looking at the extended outcomes (same hypotheses as tests 2 and 4, nothing new is tuned):
  2L  Kharkiv missile day D  ~  RU panel threat shares (l2_ru, thr_ru, tgt_ru), 24 h window ending 21:00 on D-1.
  4L  National massive day D ~  panel envoy mentions without Iran context (dip_ua_p), window ending 15:00 on D-1;
      plus the episode test of test 4 (spike days >= 5 posts and >= 3x trailing 14-day median, follow-up within k days).
Model protocol = nowcast.py: own-target lags start at D-2, expanding window, logistic C=1, min_train 60;
Δ AUC vs the baseline with a moving-block bootstrap CI (L=10). Results are split into the pre-panel history and the
live panel period so a signal that exists in only one of them is visible.

  python src/test5_history.py            # needs DATABASE_URL; prints the report, writes data/test5_history.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from features import PANEL_START, connect, design, frame
from leadlag import make_stationary, xcorr_perm
from nowcast import block_ci, fit_predict, metrics

ROOT = Path(__file__).resolve().parent.parent
START = "2025-01-01"
RNG = np.random.default_rng(11)


def oos(df, target, signals, min_train=60):
    spec = {"target": target, "signals": signals}
    F, y = design(df, spec), df[target]
    p = pd.Series(np.nan, index=df.index)
    for row in df.index:
        if row < min_train or pd.isna(y[row]):
            continue
        p[row], _ = fit_predict(F, y, (df.index < row - 1) & y.notna(), row, 1.0)
    return p


def compare(df, target, sig, out, label):
    pb, pa = oos(df, target, []), oos(df, target, [sig])
    y = df[target]
    res = {}
    for part, mask in (("all", df.d.notna()), ("pre-panel", df.d < pd.Timestamp(PANEL_START)), ("panel", df.d >= pd.Timestamp(PANEL_START))):
        m = mask & pa.notna() & pb.notna() & y.notna()
        if m.sum() < 30 or y[m].nunique() < 2:
            res[part] = {"n": int(m.sum())}
            continue
        ma, mb = metrics(y[m], pa[m]), metrics(y[m], pb[m])
        ci = block_ci(y[m].to_numpy(int), pa[m].to_numpy(), pb[m].to_numpy())
        res[part] = {"n": ma["n"], "positives": ma["positives"], "auc_base": mb["auc"], "auc_sig": ma["auc"],
                     "delta_auc": ma["auc"] - mb["auc"], "ci": ci, "brier_base": mb["brier"], "brier_sig": ma["brier"]}
        print(f"  {label:26s} {part:9s} n {ma['n']:3d} pos {ma['positives']:3d}  AUC {mb['auc']:.3f} -> {ma['auc']:.3f}  "
              f"Δ {ma['auc'] - mb['auc']:+.3f} CI {[round(x, 3) for x in ci] if ci else None}  Brier {mb['brier']:.3f} -> {ma['brier']:.3f}")
    out[label] = res


def xcorr(df, sig, target, out, label):
    d = df[[sig, target]].dropna()
    x, dx, _ = make_stationary(d[sig].to_numpy(float))
    y, dy, _ = make_stationary(d[target].to_numpy(float))
    m = min(len(x), len(y))
    r, p = xcorr_perm(x[-m:], y[-m:], 3)
    out[label] = {"n": int(m), "r": [float(v) for v in r], "p_fw": [float(v) for v in p], "differenced": [bool(dx), bool(dy)]}
    print(f"  {label:26s} n {m}  " + "  ".join(f"lag{k + 1} r={r[k]:+.2f} p={p[k]:.3f}" for k in range(3)))


def episodes(s):
    med = s.rolling(14, min_periods=5).median().shift(1).fillna(0)
    day = ((s >= 5) & (s >= 3 * med.clip(lower=1))).astype(int)
    return ((day == 1) & (day.shift(1, fill_value=0) == 0)).astype(int).to_numpy()


def follow(ev, y, k):
    idx = np.where(ev == 1)[0]
    hits = [np.nansum(y[i + 1:i + 1 + k]) > 0 for i in idx if i + k < len(y)]
    return (np.mean(hits) if hits else np.nan), len(hits)


def main():
    out = {}
    with connect() as conn:
        kh = frame(conn, 21, start=START)
        nat = frame(conn, 15, start=START)
        day = frame(conn, 24, start=START)
    for name, f, tgt in (("Kharkiv (cutoff 21)", kh, "kh_missile_day"), ("national (cutoff 15)", nat, "nat_massive")):
        have = f[f[tgt].notna()]
        print(f"{name}: {len(f)} days, target known {len(have)} ({have.d.min().date()} .. {have.d.max().date()}), "
              f"positives {int(have[tgt].sum())}, rhetoric rows {int(f.l2_ru.notna().sum())}")
    print("\n2L  Kharkiv missile day ~ RU panel threat shares (out of sample, live timing)")
    for sig in ("l2_ru", "thr_ru", "tgt_ru"):
        compare(kh, "kh_missile_day", sig, out, f"kh~{sig}")
    print("\n4L  National massive day ~ panel envoy mentions (out of sample, live timing)")
    for sig in ("dip_ua_p", "dip_p", "peace_p"):
        compare(nat, "nat_massive", sig, out, f"nat~{sig}")
    print("\ncross-correlation, calendar days (cutoff 24), circular-shift permutations, family-wise p over lags 1-3")
    for sig, tgt in (("l2_ru", "kh_missile_day"), ("thr_ru", "kh_missile_day"), ("dip_ua_p", "nat_massive")):
        xcorr(day, sig, tgt, out, f"xcorr {sig}->{tgt}")
    print("\nenvoy spike episodes (dip_ua_p, cutoff 24) -> national massive day within k days, vs circular shifts")
    d = day[day.nat_massive.notna()].reset_index(drop=True)
    ev, y = episodes(d.dip_ua_p.fillna(0)), d.nat_massive.to_numpy(float)
    print("  episodes:", ", ".join(str(x.date()) for x in d.d[ev == 1]))
    out["episodes"] = {"dates": [str(x.date()) for x in d.d[ev == 1]], "base_rate": float(np.nanmean(y))}
    for k in (1, 2, 3):
        o, n = follow(ev, y, k)
        perm = np.array([follow(np.roll(ev, RNG.integers(5, len(ev) - 5)), y, k)[0] for _ in range(3000)])
        p = float((perm >= o).mean())
        out["episodes"][f"within_{k}"] = {"rate": float(o), "n": n, "random": float(np.nanmean(perm)), "p": p}
        print(f"  within {k}: {o:.2f} (n={n}) vs random {np.nanmean(perm):.2f}, p={p:.3f}")
    dest = ROOT / "data" / "test5_history.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, indent=1, default=float))
    print("saved", dest)


if __name__ == "__main__":
    main()
