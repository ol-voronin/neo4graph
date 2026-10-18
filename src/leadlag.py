"""Lead-lag test: does a daily rhetoric signal precede strike intensity? (ideas: ADF check, expanding-window CV, no leakage)

Inputs (CSV with a `date` column): one row per day with a target intensity and a signal.
    python src/leadlag.py data.csv --signal threat_mean --target intensity --max-lag 3
    python src/leadlag.py --selftest

Method, in order:
  1. ADF stationarity test on signal and target; non-stationary series are first-differenced (reported).
  2. Cross-correlation of signal(t-k) with target(t), k = 0..max_lag, only k>=1 counts as "leading".
     p-values by circular-shift permutation (keeps autocorrelation), corrected for the number of lags by the
     max-|r| statistic across lags (family-wise error).
  3. Expanding-window forecast of "high-intensity day" (target above the TRAIN-only 75th percentile):
     baseline = yesterday's intensity + 3-day mean; full = baseline + lagged signal. Logistic regression,
     one-step-ahead, scaler fit on train only. Reports AUC of both and the difference with a block-bootstrap CI.
With ~22 days nothing here has power (see docs/evaluation_stats.md); this is infrastructure, validated on synthetic data.
"""
import argparse
import sys

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.stattools import adfuller

RNG = np.random.default_rng(7)


def make_stationary(x: np.ndarray):
    """ADF (H0: unit root). Returns (series, differenced?, p-value). Needs >= ~10 points to be meaningful."""
    if len(x) < 10 or np.nanstd(x) == 0:
        return x, False, float("nan")
    p = adfuller(x, autolag="AIC", result_object=False)[1]
    if p > 0.05:
        return np.diff(x), True, p
    return x, False, p


def xcorr_perm(sig: np.ndarray, tgt: np.ndarray, max_lag: int, n_perm: int = 2000):
    """r_k = corr(sig[t-k], tgt[t]). Circular-shift permutation; family-wise p via max |r| over lags 1..max_lag."""
    def rs(s):
        return np.array([np.corrcoef(s[:len(s) - k], tgt[k:])[0, 1] for k in range(1, max_lag + 1)])
    obs = rs(sig)
    n = len(sig)
    maxes = np.empty(n_perm)
    for i in range(n_perm):
        shift = RNG.integers(max_lag + 1, n - max_lag - 1)
        maxes[i] = np.nanmax(np.abs(rs(np.roll(sig, shift))))
    p_fw = [(np.sum(maxes >= abs(r)) + 1) / (n_perm + 1) for r in obs]
    return obs, np.array(p_fw)


def _feats(df, sig, tgt, lag):
    f = pd.DataFrame({"y_prev": df[tgt].shift(1), "y_ma3": df[tgt].shift(1).rolling(3).mean(), "sig": df[sig].shift(lag)})
    return f


def expanding_auc(df, sig, tgt, lag=1, min_train=14, q=0.75):
    """One-step-ahead. For each test day t: threshold = q-quantile of TRAIN targets only; fit on rows < t."""
    f = _feats(df, sig, tgt, lag)
    base_cols, full_cols = ["y_prev", "y_ma3"], ["y_prev", "y_ma3", "sig"]
    y_true, p_base, p_full = [], [], []
    for t in range(min_train, len(df)):
        tr = slice(0, t)
        thr = np.nanquantile(df[tgt].iloc[:t], q)
        ytr = (df[tgt].iloc[:t] > thr).astype(int)
        ftr = f.iloc[:t]
        ok = ftr.notna().all(axis=1)
        if ytr[ok].nunique() < 2 or not f.iloc[t].notna().all():
            continue
        out = []
        for cols in (base_cols, full_cols):
            sc = StandardScaler().fit(ftr.loc[ok, cols])
            m = LogisticRegression(C=0.5).fit(sc.transform(ftr.loc[ok, cols]), ytr[ok])
            out.append(m.predict_proba(sc.transform(f.iloc[[t]][cols]))[0, 1])
        y_true.append(int(df[tgt].iloc[t] > thr)); p_base.append(out[0]); p_full.append(out[1])
    y_true, p_base, p_full = map(np.array, (y_true, p_base, p_full))
    if len(y_true) < 8 or len(set(y_true)) < 2:
        return None
    a0, a1 = roc_auc_score(y_true, p_base), roc_auc_score(y_true, p_full)
    # moving-block bootstrap of the AUC difference
    n, b, diffs = len(y_true), 3, []
    for _ in range(1000):
        idx = np.concatenate([np.arange(s, s + b) % n for s in RNG.integers(0, n, n // b + 1)])[:n]
        if len(set(y_true[idx])) < 2:
            continue
        diffs.append(roc_auc_score(y_true[idx], p_full[idx]) - roc_auc_score(y_true[idx], p_base[idx]))
    lo, hi = np.percentile(diffs, [2.5, 97.5]) if diffs else (np.nan, np.nan)
    return {"n_test": n, "auc_base": a0, "auc_full": a1, "delta": a1 - a0, "delta_ci": (lo, hi)}


def run(df, sig, tgt, max_lag):
    x, dx, px = make_stationary(df[sig].to_numpy(float))
    y, dy, py = make_stationary(df[tgt].to_numpy(float))
    m = min(len(x), len(y)); x, y = x[-m:], y[-m:]
    print(f"ADF p(signal)={px:.3f}{' -> differenced' if dx else ''}; p(target)={py:.3f}{' -> differenced' if dy else ''}; n={m}")
    r, p = xcorr_perm(x, y, max_lag)
    for k, (rk, pk) in enumerate(zip(r, p), 1):
        print(f"lag {k}: r={rk:+.2f}  p_fw={pk:.3f}")
    res = expanding_auc(df, sig, tgt)
    print("expanding AUC:", res if res else "not enough data")
    return r, p, res


def selftest():
    """Planted lead (signal drives target next day) must be found; pure noise must not (false-positive rate ~ alpha)."""
    n, trials, hit_plant, hit_noise = 120, 40, 0, 0
    for _ in range(trials):
        s = RNG.normal(size=n)
        t_plant = 0.7 * np.r_[0, s[:-1]] + RNG.normal(size=n)
        t_noise = RNG.normal(size=n)
        _, p1 = xcorr_perm(s, t_plant, 3, 400)
        _, p0 = xcorr_perm(s, t_noise, 3, 400)
        hit_plant += p1.min() < 0.05
        hit_noise += p0.min() < 0.05
    print(f"power (planted lag-1, n={n}): {hit_plant}/{trials};  false positives on noise: {hit_noise}/{trials} (expect <= ~{0.05*trials:.0f})")
    # AUC path: planted signal must beat the baseline, noise must not (mean delta over trials)
    dl, dn = [], []
    for _ in range(10):
        n2 = 150
        sg = RNG.normal(size=n2)
        base = np.cumsum(RNG.normal(size=n2)) * 0.1
        df_p = pd.DataFrame({"sig": sg, "y": base + 1.2 * np.r_[0, sg[:-1]] + RNG.normal(size=n2) * 0.5})
        df_n = pd.DataFrame({"sig": sg, "y": base + RNG.normal(size=n2) * 0.5})
        for df_, acc in ((df_p, dl), (df_n, dn)):
            r_ = expanding_auc(df_, "sig", "y")
            if r_:
                acc.append(r_["delta"])
    print(f"AUC gain from signal: planted {np.mean(dl):+.3f} vs noise {np.mean(dn):+.3f}")
    # short series: shows the data bottleneck
    hits = 0
    for _ in range(trials):
        s = RNG.normal(size=22); t = 0.7 * np.r_[0, s[:-1]] + RNG.normal(size=22)
        hits += xcorr_perm(s, t, 3, 400)[1].min() < 0.05
    print(f"power with n=22: {hits}/{trials}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?"); ap.add_argument("--signal"); ap.add_argument("--target")
    ap.add_argument("--max-lag", type=int, default=3); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.csv:
        selftest(); sys.exit()
    d = pd.read_csv(a.csv, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    run(d, a.signal, a.target, a.max_lag)
