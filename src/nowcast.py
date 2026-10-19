"""Prospective test of the rhetoric hypothesis (docs/architecture.md, 6.6).

Every forecast is written BEFORE its outcome window opens and is never changed; scoring happens later. This is the test
that cannot be fitted after the fact. It is research output, not an air-raid warning.

  python src/nowcast.py predict [--now 2026-10-20T16:10] [--dry-run]   # issue all models whose window is open
  python src/nowcast.py evaluate                                      # fill outcomes for past target days
  python src/nowcast.py backtest [--model kh_missile_v1]              # same code path over history (expanding window)
  python src/nowcast.py report                                        # prospective scores so far, candidate vs baseline

Model specs: models/*.json (frozen; a change means a new file with a new name, the old history stays).
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from features import connect, design, frame

ROOT = Path(__file__).resolve().parent.parent
KYIV = "Europe/Kyiv"
DDL = """
CREATE TABLE IF NOT EXISTS predictions (
  model text NOT NULL, target_date date NOT NULL, issued_at timestamptz NOT NULL DEFAULT now(),
  p real NOT NULL, n_train int, features jsonb, features_hash text, spec_hash text,
  outcome smallint, scored_at timestamptz,
  PRIMARY KEY (model, target_date))"""


def specs(only=None):
    out = []
    for f in sorted((ROOT / "models").glob("*.json")):
        s = json.loads(f.read_text())
        s["spec_hash"] = hashlib.sha1(f.read_bytes()).hexdigest()[:12]
        if not only or s["name"] == only:
            out.append(s)
    return out


def fit_predict(F, y, train_mask, row, C):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    tr = F[train_mask].dropna()
    ytr = y[tr.index]
    if len(tr) < 10 or ytr.nunique() < 2 or F.loc[[row]].isna().any(axis=None):
        return None, len(tr)
    sc = StandardScaler().fit(tr)
    m = LogisticRegression(C=C, max_iter=500).fit(sc.transform(tr), ytr.astype(int))
    return float(m.predict_proba(sc.transform(F.loc[[row]]))[0, 1]), len(tr)


def window_open(spec, target_date, now):
    """Issue only after the feature window has closed (cutoff on D-1) and before the outcome window opens (deadline)."""
    d1 = pd.Timestamp(target_date) - pd.Timedelta(days=1)
    return d1 + pd.Timedelta(hours=spec["cutoff_hour"]) <= now < d1 + pd.Timedelta(hours=spec["deadline_hour"])


def cmd_predict(a):
    now = pd.Timestamp(a.now) if a.now else pd.Timestamp.now(tz=KYIV).tz_localize(None)
    target = (now + pd.Timedelta(days=1)).normalize()
    with connect() as conn:
        if not a.dry_run:
            conn.execute(DDL)
        for s in specs(a.model):
            if not window_open(s, target, now):
                print(f"{s['name']}: window for {target.date()} not open at {now:%Y-%m-%d %H:%M} (cutoff {s['cutoff_hour']}:00, deadline {s['deadline_hour']}:00 on D-1)")
                continue
            df = frame(conn, s["cutoff_hour"], until=target)
            F, y = design(df, s), df[s["target"]]
            row = df.index[df.d == target][0]
            # train on days whose outcome is already known and that end before the issue time
            p, n = fit_predict(F, y, (df.d < target - pd.Timedelta(days=1)) & y.notna(), row, s["C"])
            if p is None:
                print(f"{s['name']}: not enough data (n_train {n}) or missing features for {target.date()}: {F.loc[row].to_dict()}")
                continue
            feats = {k: (None if pd.isna(v) else round(float(v), 6)) for k, v in F.loc[row].items()}
            fh = hashlib.sha1(json.dumps(feats, sort_keys=True).encode()).hexdigest()[:12]
            print(f"{s['name']}: P({s['target']} on {target.date()}) = {p:.3f}  n_train {n}  {feats}")
            if not a.dry_run:
                conn.execute("INSERT INTO predictions(model, target_date, p, n_train, features, features_hash, spec_hash) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                             "ON CONFLICT (model, target_date) DO NOTHING",
                             (s["name"], target.date(), p, n, json.dumps(feats), fh, s["spec_hash"]))
        conn.commit()


def cmd_evaluate(a):
    with connect() as conn:
        conn.execute(DDL)
        df = frame(conn, 24)
        names = {s["name"]: s["target"] for s in specs()}
        rows = conn.execute("SELECT model, target_date FROM predictions WHERE outcome IS NULL").fetchall()
        n = 0
        for model, d in rows:
            hit = df[df.d == pd.Timestamp(d)]
            if model not in names or hit.empty or pd.isna(hit[names[model]].iloc[0]):
                continue
            conn.execute("UPDATE predictions SET outcome = %s, scored_at = now() WHERE model = %s AND target_date = %s AND outcome IS NULL",
                         (int(hit[names[model]].iloc[0]), model, d))
            n += 1
        conn.commit()
        print(f"scored {n} of {len(rows)} open predictions")


def metrics(y, p):
    from sklearn.metrics import brier_score_loss, roc_auc_score
    y, p = np.asarray(y, int), np.asarray(p, float)
    out = {"n": int(len(y)), "positives": int(y.sum()), "brier": float(brier_score_loss(y, p)) if len(y) else None}
    out["auc"] = float(roc_auc_score(y, p)) if len(set(y)) == 2 else None
    return out


def block_ci(y, pa, pb, B=1000, L=10, seed=7):
    """Moving-block bootstrap CI of AUC(candidate) - AUC(baseline), same blocks for both."""
    from sklearn.metrics import roc_auc_score
    rng, n, diffs = np.random.default_rng(seed), len(y), []
    if n < 2 * L:
        return None
    for _ in range(B):
        idx = np.concatenate([np.arange(s, s + L) for s in rng.integers(0, n - L + 1, n // L + 1)])[:n]
        if len(set(y[idx])) == 2:
            diffs.append(roc_auc_score(y[idx], pa[idx]) - roc_auc_score(y[idx], pb[idx]))
    return [float(x) for x in np.percentile(diffs, [2.5, 97.5])] if diffs else None


def cmd_backtest(a):
    """Expanding window over history with exactly the live code path; for orientation, not for the verdict."""
    res = {}
    with connect() as conn:
        for s in specs(a.model):
            df = frame(conn, s["cutoff_hour"])
            F, y = design(df, s), df[s["target"]]
            ps = pd.Series(np.nan, index=df.index)
            for row in df.index:
                if row < s["min_train"] or pd.isna(y[row]):
                    continue
                ps[row], _ = fit_predict(F, y, (df.index < row - 1) & y.notna(), row, s["C"])
            ok = ps.notna()
            res[s["name"]] = {"spec": s, "y": y[ok].astype(int).tolist(), "p": ps[ok].tolist(), "d": df.d[ok].dt.strftime("%Y-%m-%d").tolist(),
                              **metrics(y[ok], ps[ok])}
    for name, r in res.items():
        line = f"{name:22s} n {r['n']:4d}  pos {r['positives']:3d}  AUC {r['auc'] if r['auc'] is None else round(r['auc'], 3)}  Brier {round(r['brier'], 3)}"
        base = res.get(r["spec"].get("compare_to"))
        if base:
            common = sorted(set(r["d"]) & set(base["d"]))
            ia = [r["d"].index(x) for x in common]
            ib = [base["d"].index(x) for x in common]
            yy = np.array(r["y"])[ia]
            pa, pb = np.array(r["p"])[ia], np.array(base["p"])[ib]
            ma, mb = metrics(yy, pa), metrics(yy, pb)
            if ma["auc"] is not None and mb["auc"] is not None:
                line += f"  Δ AUC vs {base['spec']['name']} {ma['auc'] - mb['auc']:+.3f} CI {block_ci(yy, pa, pb)}"
        print(line)
    out = ROOT / "data" / "backtest_nowcast.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "spec"} for k, v in res.items()}, indent=1))
    print("saved", out)


def cmd_report(a):
    with connect() as conn:
        conn.execute(DDL)
        df = pd.DataFrame(conn.execute("SELECT model, target_date, p, outcome FROM predictions WHERE outcome IS NOT NULL ORDER BY target_date").fetchall(),
                          columns=["model", "d", "p", "y"])
    if df.empty:
        print("no scored predictions yet")
        return
    for s in specs():
        m = df[df.model == s["name"]]
        line = f"{s['name']:22s} " + json.dumps(metrics(m.y, m.p))
        base = df[df.model == s.get("compare_to", "")]
        if not base.empty:
            j = m.merge(base, on="d", suffixes=("", "_b"))
            mb, ma = metrics(j.y, j.p_b), metrics(j.y, j.p)
            if ma["auc"] is not None and mb["auc"] is not None:
                line += f"  Δ AUC {ma['auc'] - mb['auc']:+.3f} CI {block_ci(j.y.to_numpy(int), j.p.to_numpy(), j.p_b.to_numpy())} (n {len(j)})"
        print(line)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["predict", "evaluate", "backtest", "report"])
    ap.add_argument("--model")
    ap.add_argument("--now", help="Kyiv local time, for testing the issue window")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    {"predict": cmd_predict, "evaluate": cmd_evaluate, "backtest": cmd_backtest, "report": cmd_report}[a.cmd](a)
