"""Prospective test of the rhetoric hypothesis (docs/architecture.md, 6.6).

Every forecast is written BEFORE its outcome window opens and is never changed; scoring happens later. This is the test
that cannot be fitted after the fact. It is research output, not an air-raid warning.

  python src/nowcast.py predict [--now 2026-10-20T16:10] [--dry-run]   # issue all models whose window is open
  python src/nowcast.py evaluate                                      # fill outcomes for past target days
  python src/nowcast.py backtest [--model kh_missile_v1]              # same code path over history (expanding window)
  python src/nowcast.py backtest --model ua_oblast_massive_v1 --controls 50   # + shuffled-date control for the signals
  python src/nowcast.py report                                        # prospective scores so far, candidate vs baseline
  python src/nowcast.py live [--now ...] [--dry-run]                  # hourly current estimate (live_estimates, not the test)

Model specs: models/*.json (frozen; a change means a new file with a new name, the old history stays).
A spec with "panel": "oblast" is a pooled model over all oblasts (long format, features.design_long): one forecast per
region and day, region as a feature plus a shrunk per-region base rate. Other specs are single-series models; their
rows carry region_id 'kharkivska' (kh_*) or 'ukraine' (national).
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from features import connect, design, design_long, frame, frame_long

ROOT = Path(__file__).resolve().parent.parent
KYIV = "Europe/Kyiv"
DDL = """
CREATE TABLE IF NOT EXISTS predictions (
  model text NOT NULL, region_id text NOT NULL DEFAULT 'ukraine', target_date date NOT NULL, issued_at timestamptz NOT NULL DEFAULT now(),
  p real NOT NULL, n_train int, features jsonb, features_hash text, spec_hash text,
  outcome smallint, scored_at timestamptz,
  PRIMARY KEY (model, region_id, target_date))"""


def is_panel(spec):
    return spec.get("panel") == "oblast"


def region_of(spec):
    """Region of a single-series model. Frozen v1 specs predate the field, so infer it from the target."""
    return spec.get("region") or ("kharkivska" if spec["target"].startswith("kh_") else "ukraine")


def has_region(conn):
    """predictions.region_id exists (migration 015). Before it, only single-series models can be written."""
    return bool(conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'predictions' AND column_name = 'region_id'").fetchone())


def get_frame(conn, spec, until=None, cutoff=None):
    c = spec["cutoff_hour"] if cutoff is None else cutoff
    if is_panel(spec):
        return frame_long(conn, c, until=until, ee_min_extracted=spec.get("ee_min_extracted", 100))
    return frame(conn, c, until=until)


def get_design(df, spec, signal_shift=0):
    return design_long(df, spec, signal_shift) if is_panel(spec) else design(df, spec)


def specs(only=None):
    out = []
    for f in sorted((ROOT / "models").glob("*.json")):
        s = json.loads(f.read_text())
        s["spec_hash"] = hashlib.sha1(f.read_bytes()).hexdigest()[:12]
        if not only or s["name"] == only:
            out.append(s)
    return out


def fit_predict(F, y, train_mask, row, C):
    p, n = fit_predict_rows(F, y, train_mask, [row], C)
    return (None if p is None else float(p[0])), n


def fit_predict_rows(F, y, train_mask, rows, C):
    """Logistic regression on the rows in train_mask with known features; predict `rows` (all must have features)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    tr = F[train_mask].dropna()
    ytr = y[tr.index]
    if len(tr) < 10 or ytr.nunique() < 2 or F.loc[rows].isna().any(axis=None):
        return None, len(tr)
    sc = StandardScaler().fit(tr)
    m = LogisticRegression(C=C, max_iter=1000).fit(sc.transform(tr), ytr.astype(int))
    return m.predict_proba(sc.transform(F.loc[rows]))[:, 1], len(tr)


def window_open(spec, target_date, now):
    """Issue only after the feature window has closed (cutoff on D-1) and before the outcome window opens (deadline)."""
    d1 = pd.Timestamp(target_date) - pd.Timedelta(days=1)
    return d1 + pd.Timedelta(hours=spec["cutoff_hour"]) <= now < d1 + pd.Timedelta(hours=spec["deadline_hour"])


def _feats(F, row):
    feats = {k: (None if pd.isna(v) else round(float(v), 6)) for k, v in F.loc[row].items() if not (k.startswith("reg_") and v == 0)}
    return feats, hashlib.sha1(json.dumps(feats, sort_keys=True).encode()).hexdigest()[:12]


def cmd_predict(a):
    now = pd.Timestamp(a.now) if a.now else pd.Timestamp.now(tz=KYIV).tz_localize(None)
    target = (now + pd.Timedelta(days=1)).normalize()
    with connect() as conn:
        if not a.dry_run:
            conn.execute(DDL)
        regional = has_region(conn)
        for s in specs(a.model):
            if not window_open(s, target, now):
                print(f"{s['name']}: window for {target.date()} not open at {now:%Y-%m-%d %H:%M} (cutoff {s['cutoff_hour']}:00, deadline {s['deadline_hour']}:00 on D-1)")
                continue
            if is_panel(s) and not regional:
                print(f"{s['name']}: predictions.region_id missing (apply db/migrations/015_oblast_daily.sql): skipped")
                continue
            df = get_frame(conn, s, until=target)
            F, y = get_design(df, s), df[s["target"]]
            rows = list(df.index[df.d == target])
            # train on days whose outcome is already known and that end before the issue time
            train = (df.d < target - pd.Timedelta(days=1)) & y.notna()
            if is_panel(s):  # predict every region that has its features; a region without them gets no forecast
                rows = [r for r in rows if not F.loc[r].isna().any()]
            p, n = fit_predict_rows(F, y, train, rows, s["C"]) if rows else (None, 0)
            if p is None:
                print(f"{s['name']}: not enough data (n_train {n}) or missing features for {target.date()}")
                continue
            for row, pr in zip(rows, p):
                reg = df.region_id[row] if is_panel(s) else region_of(s)
                feats, fh = _feats(F, row)
                print(f"{s['name']} [{reg}]: P({s['target']} on {target.date()}) = {pr:.3f}  n_train {n}" + ("" if is_panel(s) else f"  {feats}"))
                if a.dry_run:
                    continue
                if regional:
                    conn.execute("INSERT INTO predictions(model, region_id, target_date, p, n_train, features, features_hash, spec_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                                 "ON CONFLICT (model, region_id, target_date) DO NOTHING",
                                 (s["name"], reg, target.date(), float(pr), n, json.dumps(feats), fh, s["spec_hash"]))
                else:  # before migration 015
                    conn.execute("INSERT INTO predictions(model, target_date, p, n_train, features, features_hash, spec_hash) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                                 "ON CONFLICT (model, target_date) DO NOTHING",
                                 (s["name"], target.date(), float(pr), n, json.dumps(feats), fh, s["spec_hash"]))
        conn.commit()


LIVE_MODELS = ("ua_oblast_massive3_v1", "ua_oblast_massive3_base_v1")


def cmd_live(a):
    """Current estimate for the next Kyiv day: the frozen v3 specs refitted with the rhetoric window ending at this hour
    (cutoff = current hour), trained on days with a known outcome. Written to live_estimates; predictions are untouched."""
    now = pd.Timestamp(a.now) if a.now else pd.Timestamp.now(tz=KYIV).tz_localize(None)
    target, cutoff = (now + pd.Timedelta(days=1)).normalize(), max(1, now.hour)
    with connect() as conn:
        for s in specs():
            if s["name"] not in LIVE_MODELS:
                continue
            df = get_frame(conn, s, until=target, cutoff=cutoff)
            F, y = get_design(df, s), df[s["target"]]
            rows = [r for r in df.index[df.d == target] if not F.loc[r].isna().any()]
            p, n = fit_predict_rows(F, y, (df.d < target - pd.Timedelta(days=1)) & y.notna(), rows, s["C"]) if rows else (None, 0)
            if p is None:
                print(f"{s['name']}: not enough data (n_train {n}) or missing features for {target.date()}")
                continue
            vals = [(s["name"], df.region_id[r], target.date(), cutoff, float(pr), n, json.dumps(_feats(F, r)[0])) for r, pr in zip(rows, p)]
            print(f"{s['name']} {target.date()} cutoff {cutoff}:00 n_train {n}: " + ", ".join(f"{v[1]} {v[4]:.0%}" for v in sorted(vals, key=lambda v: -v[4])))
            if not a.dry_run:
                with conn.cursor() as cur:
                    cur.executemany("INSERT INTO live_estimates(model, region_id, target_date, cutoff_hour, p, n_train, features) VALUES (%s,%s,%s,%s,%s,%s,%s)", vals)
        conn.commit()


def cmd_evaluate(a):
    with connect() as conn:
        conn.execute(DDL)
        regional = has_region(conn)
        by_name = {s["name"]: s for s in specs()}
        rows = conn.execute("SELECT model, " + ("region_id" if regional else "'ukraine'") + ", target_date FROM predictions WHERE outcome IS NULL").fetchall()
        frames, n = {}, 0
        for model, reg, d in rows:
            s = by_name.get(model)
            if s is None:
                continue
            key = "panel" if is_panel(s) else "series"
            if key not in frames:
                frames[key] = get_frame(conn, s, cutoff=24)
            df = frames[key]
            hit = df[(df.d == pd.Timestamp(d)) & ((df.region_id == reg) if is_panel(s) else True)]
            if hit.empty or pd.isna(hit[s["target"]].iloc[0]):
                continue
            where = "model = %s AND target_date = %s" + (" AND region_id = %s" if regional else "")
            conn.execute(f"UPDATE predictions SET outcome = %s, scored_at = now() WHERE {where} AND outcome IS NULL",
                         (int(hit[s["target"]].iloc[0]), model, d, *([reg] if regional else [])))
            n += 1
        conn.commit()
        print(f"scored {n} of {len(rows)} open predictions")


def metrics(y, p, groups=None):
    """AUC / Brier; for a panel also the within-region AUC (mean of per-region AUCs weighted by positives), because a
    pooled AUC is inflated by between-region base-rate differences that any region dummy captures."""
    from sklearn.metrics import brier_score_loss, roc_auc_score
    y, p = np.asarray(y, int), np.asarray(p, float)
    out = {"n": int(len(y)), "positives": int(y.sum()), "brier": float(brier_score_loss(y, p)) if len(y) else None}
    out["auc"] = float(roc_auc_score(y, p)) if len(set(y)) == 2 else None
    if groups is not None:
        g = np.asarray(groups)
        aucs = [(roc_auc_score(y[g == r], p[g == r]), y[g == r].sum()) for r in np.unique(g) if len(set(y[g == r])) == 2]
        out["auc_within"] = float(sum(a * w for a, w in aucs) / sum(w for _, w in aucs)) if aucs else None
    return out


def block_ci(y, pa, pb, B=1000, L=10, seed=7, days=None):
    """Moving-block bootstrap CI of AUC(candidate) - AUC(baseline), same blocks for both. With `days` (panel rows),
    blocks are drawn over dates and carry every region of those dates, so cross-region correlation is kept."""
    from sklearn.metrics import roc_auc_score
    rng, diffs = np.random.default_rng(seed), []
    if days is None:
        units = [np.array([i]) for i in range(len(y))]
    else:
        days = np.asarray(days)
        units = [np.flatnonzero(days == d) for d in np.unique(days)]
    n = len(units)
    if n < 2 * L:
        return None
    for _ in range(B):
        starts = rng.integers(0, n - L + 1, n // L + 1)
        idx = np.concatenate([units[i] for s in starts for i in range(s, s + L)][:n])
        if len(set(y[idx])) == 2:
            diffs.append(roc_auc_score(y[idx], pa[idx]) - roc_auc_score(y[idx], pb[idx]))
    return [float(x) for x in np.percentile(diffs, [2.5, 97.5])] if diffs else None


def backtest_spec(df, s, signal_shift=0):
    """Expanding window with exactly the live code path. Returns a frame of (d, region_id, y, p)."""
    F, y = get_design(df, s, signal_shift), df[s["target"]]
    out = []
    if is_panel(s):
        days = np.sort(df.d.unique())
        for i, d in enumerate(days):
            if i < s["min_train"]:
                continue
            rows = [r for r in df.index[(df.d == d) & y.notna()] if not F.loc[r].isna().any()]
            if not rows:
                continue
            p, _ = fit_predict_rows(F, y, (df.d < d - pd.Timedelta(days=1)) & y.notna(), rows, s["C"])
            if p is not None:
                out += [(d, df.region_id[r], int(y[r]), float(pr)) for r, pr in zip(rows, p)]
    else:
        for row in df.index:
            if row < s["min_train"] or pd.isna(y[row]):
                continue
            p, _ = fit_predict(F, y, (df.index < row - 1) & y.notna(), row, s["C"])
            if p is not None:
                out.append((df.d[row], region_of(s), int(y[row]), p))
    return pd.DataFrame(out, columns=["d", "region_id", "y", "p"])


def compare(r, base, panel):
    """Candidate vs baseline on the common (d, region) rows: Δ AUC, block-bootstrap CI."""
    j = r.merge(base, on=["d", "region_id", "y"], suffixes=("", "_b"))
    if j.empty:
        return None, None, j
    ma, mb = metrics(j.y, j.p), metrics(j.y, j.p_b)
    if ma["auc"] is None or mb["auc"] is None:
        return None, None, j
    ci = block_ci(j.y.to_numpy(int), j.p.to_numpy(), j.p_b.to_numpy(), days=j.d.to_numpy() if panel else None)
    return ma["auc"] - mb["auc"], ci, j


def cmd_backtest(a):
    """Expanding window over history with exactly the live code path; for orientation, not for the verdict.
    --controls N: rerun each candidate with its signals circularly shifted by N random offsets (>= 7 days), the
    shuffled-date control of docs/leadlag_results; reports where the real Δ AUC falls in that null distribution."""
    res, frames = {}, {}
    with connect() as conn:
        all_specs = {s["name"]: s for s in specs()}
        for s in specs(a.model):
            todo = [s] + ([all_specs[s["compare_to"]]] if a.model and s.get("compare_to") in all_specs else [])
            for t in todo:
                key = (is_panel(t), t["cutoff_hour"], t.get("ee_min_extracted"))
                if key not in frames:
                    frames[key] = get_frame(conn, t)
                if t["name"] not in res:
                    res[t["name"]] = (t, frames[key], backtest_spec(frames[key], t))
    report = {}
    for name, (s, df, r) in res.items():
        m = metrics(r.y, r.p, r.region_id if is_panel(s) else None) if len(r) else {"n": 0}
        line = f"{name:24s} n {m['n']:5d}  pos {m.get('positives', 0):4d}  AUC {m.get('auc') if m.get('auc') is None else round(m['auc'], 3)}" \
               + (f"  within-region AUC {m.get('auc_within') if m.get('auc_within') is None else round(m['auc_within'], 3)}" if is_panel(s) else "") \
               + (f"  Brier {round(m['brier'], 3)}" if m.get("brier") is not None else "")
        report[name] = {**m, "rows": r.assign(d=r.d.astype(str)).to_dict("list")}
        base = res.get(s.get("compare_to"))
        if base and len(r):
            delta, ci, _ = compare(r, base[2], is_panel(s))
            if delta is not None:
                line += f"  Δ AUC vs {base[0]['name']} {delta:+.3f} CI {ci}"
                report[name]["delta_auc"], report[name]["delta_ci"] = delta, ci
                if a.controls and (s.get("signals") or s.get("region_signals")):
                    rng = np.random.default_rng(11)
                    n_days = df.d.nunique()
                    null = []
                    for k in rng.integers(7, max(8, n_days - 7), a.controls):
                        dk, _, _ = compare(backtest_spec(df, s, int(k)), base[2], is_panel(s))
                        if dk is not None:
                            null.append(dk)
                    if null:
                        pv = (1 + sum(x >= delta for x in null)) / (1 + len(null))
                        line += f"  shuffled control: mean Δ {np.mean(null):+.3f}, p {pv:.3f} (n {len(null)})"
                        report[name]["control_null"], report[name]["control_p"] = null, pv
        print(line)
    out = ROOT / "data" / "backtest_nowcast.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str))
    print("saved", out)


def cmd_report(a):
    with connect() as conn:
        conn.execute(DDL)
        reg = "region_id" if has_region(conn) else "'ukraine'"
        df = pd.DataFrame(conn.execute(f"SELECT model, {reg}, target_date, p, outcome FROM predictions WHERE outcome IS NOT NULL ORDER BY target_date").fetchall(),
                          columns=["model", "region_id", "d", "p", "y"])
    if df.empty:
        print("no scored predictions yet")
        return
    for s in specs():
        m = df[df.model == s["name"]].drop(columns="model")
        line = f"{s['name']:24s} " + json.dumps(metrics(m.y, m.p, m.region_id if is_panel(s) else None))
        base = df[df.model == s.get("compare_to", "")].drop(columns="model")
        if not m.empty and not base.empty:
            if not is_panel(s):  # region_id of single-series rows may differ between the pair before/after migration 015
                m, base = m.assign(region_id="-"), base.assign(region_id="-")
            delta, ci, j = compare(m, base, is_panel(s))
            if delta is not None:
                line += f"  Δ AUC {delta:+.3f} CI {ci} (n {len(j)})"
        print(line)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["predict", "evaluate", "backtest", "report", "live"])
    ap.add_argument("--model")
    ap.add_argument("--now", help="Kyiv local time, for testing the issue window")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--controls", type=int, default=0, help="backtest: number of shuffled-date control runs per candidate")
    a = ap.parse_args()
    {"predict": cmd_predict, "evaluate": cmd_evaluate, "backtest": cmd_backtest, "report": cmd_report, "live": cmd_live}[a.cmd](a)
