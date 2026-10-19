"""Test 4: do massive strikes cluster around US-envoy diplomacy (Witkoff / Kushner)?

Two event definitions, both pre-registered here before looking at the outcome:
  (a) confirmed in-person visits inside the panel window: Moscow 2026-09-05, Kyiv 2026-09-06 (one episode, so descriptive only);
  (b) "diplomacy days": days when the corpus mentions Witkoff/Kushner unusually often (>= 5 posts and >= 3x the trailing
      14-day median) — this is objective, recurs ~8 times, and lets us test with permutations.
Outcome: national "massive" day from Air Force summaries: launched missiles >= 20 OR drones >= 400.
Stat: share of event days followed by a massive day within 1..3 days vs. the same share for random day sets of the same
size (circular shifts keep the autocorrelation of the strike series). Also the simple lag table.
"""
import numpy as np
import pandas as pd

RNG = np.random.default_rng(11)
df = pd.read_csv("data/national_daily.csv", parse_dates=["date"]).sort_values("date").reset_index(drop=True)
df["massive"] = ((df.missiles >= 20) | (df.drones >= 400)).astype(int)
med = df.dip.rolling(14, min_periods=5).median().shift(1).fillna(0)
df["dip_day"] = ((df.dip >= 5) & (df.dip >= 3 * med.clip(lower=1))).astype(int)
# collapse runs: an episode starts on the first dip_day after a non-dip day
df["episode"] = ((df.dip_day == 1) & (df.dip_day.shift(1, fill_value=0) == 0)).astype(int)
n = len(df)
print(f"days {n}, massive days {df.massive.sum()} (base rate {df.massive.mean():.2f}), diplomacy episodes {df.episode.sum()}:")
for d in df[df.episode == 1].date:
    i = df.index[df.date == d][0]
    win = df.iloc[i:i + 5]
    print("  ", d.date(), "dip=", int(df.dip[i]), "| next 4 days missiles:", list(win.missiles[1:]), "drones:", list(win.drones[1:]))


def follow_rate(ev, k=3):
    idx = np.where(ev == 1)[0]
    hits = 0
    for i in idx:
        if df.massive.values[i + 1:i + 1 + k].sum() > 0:
            hits += 1
    return hits / max(1, len(idx)), len(idx)


obs, m = follow_rate(df.episode.values)
perm = []
for _ in range(5000):
    shift = RNG.integers(5, n - 5)
    perm.append(follow_rate(np.roll(df.episode.values, shift))[0])
perm = np.array(perm)
print(f"\nP(massive within 3 days | diplomacy episode) = {obs:.2f} (n={m}); random-days expectation {perm.mean():.2f}, p = {(perm >= obs).mean():.3f}")
for k in (1, 2, 3, 5, 7):
    o, _ = follow_rate(df.episode.values, k)
    p = np.array([follow_rate(np.roll(df.episode.values, RNG.integers(5, n - 5)), k)[0] for _ in range(2000)])
    print(f"  within {k} day(s): {o:.2f} vs {p.mean():.2f}, p={(p >= o).mean():.3f}")
# ---- part 1b: what the spikes were about, and Putin–Trump calls as a cleaner event ----
# dip_ua / dip_ir: envoy mentions without / with Iran-Pakistan context (the April spikes were the Islamabad talks, not Ukraine);
# call: Kremlin (Ushakov) readout of a Putin–Trump phone call, by call date.
def episodes(col, thr=5):
    med = df[col].rolling(14, min_periods=5).median().shift(1).fillna(0)
    day = ((df[col] >= thr) & (df[col] >= 3 * med.clip(lower=1))).astype(int)
    return ((day == 1) & (day.shift(1, fill_value=0) == 0)).astype(int).values
print("\nevent series → P(massive within k days) vs random dates:")
for label, ev in (("envoy spikes, Ukraine context", episodes("dip_ua")), ("envoy spikes, Iran context", episodes("dip_ir")), ("Putin–Trump calls", df.call.values)):
    dates = [str(d.date()) for d in df.date[ev == 1]]
    print(f"  {label} (n={ev.sum()}): {', '.join(dates)}")
    for k in (1, 2, 3, 5):
        o, _ = follow_rate(ev, k)
        pp = np.array([follow_rate(np.roll(ev, RNG.integers(5, n - 5)), k)[0] for _ in range(2000)])
        print(f"     within {k}: {o:.2f} vs {pp.mean():.2f}, p={(pp >= o).mean():.3f}")
for d in df.date[df.call == 1]:
    i = df.index[df.date == d][0]
    print("  call", d.date(), "| next 5 days missiles:", list(df.missiles[i + 1:i + 6]), "drones:", list(df.drones[i + 1:i + 6]))

# before vs after: is the strike series calmer on the episode days themselves (pauses during talks)?
on = df.massive[df.dip_day == 1].mean()
print(f"\nmassive rate ON diplomacy days: {on:.2f} vs overall {df.massive.mean():.2f}")
# continuous version: corr of dip(t-k) with missiles(t)
for k in (0, 1, 2, 3):
    print(f"corr(dip[t-{k}], missiles[t]) = {df.dip.shift(k).corr(df.missiles):+.2f}   corr with drones = {df.dip.shift(k).corr(df.drones):+.2f}")
# the one confirmed visit
i = df.index[df.date == "2026-09-05"][0]
print("\nconfirmed visit (Moscow 09-05, Kyiv 09-06), national missiles/drones by day:")
for j in range(i - 4, i + 9):
    print(f"  {df.date[j].date()} {int(df.missiles[j]):3d} {int(df.drones[j]):4d}  dip={int(df.dip[j])}")


# ---------- part 2: does the diplomacy count help an out-of-sample day-ahead model? (protocol of tests 2/3) ----------
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.preprocessing import StandardScaler

kh = pd.read_csv("data/daily_rhetoric.csv", parse_dates=["date"])
df = df.merge(kh[["date", "missiles", "thr_ru", "idx"]].rename(columns={"missiles": "kh_missiles"}), on="date", how="left")
df["kh_missile_day"] = (df.kh_missiles > 0).astype(int)
df["dip_share"] = df.dip / df.posts.clip(lower=1)
df["peace_share"] = df.peace / df.posts.clip(lower=1)


def oos(target, sig, min_train=30):
    y = df[target].values
    F = pd.DataFrame({"y1": df[target].shift(1), "y3": df[target].shift(1).rolling(3).mean(), "y7": df[target].shift(1).rolling(7).mean()})
    if sig:
        F["s1"] = df[sig].shift(1)
        F["s3"] = df[sig].shift(1).rolling(3).mean()
    p = np.full(len(df), np.nan)
    for t in range(min_train, len(df)):
        tr = F.iloc[:t].dropna()
        if len(set(y[tr.index])) < 2 or F.iloc[t].isna().any():
            continue
        sc = StandardScaler().fit(tr)
        m = LogisticRegression(C=1.0, max_iter=500).fit(sc.transform(tr), y[tr.index])
        p[t] = m.predict_proba(sc.transform(F.iloc[[t]]))[0, 1]
    return p, y


def block_ci(pa, pb, y, mask, B=600, L=10):
    ii = np.where(mask)[0]; n = len(ii); out = []
    for _ in range(B):
        starts = RNG.integers(0, n - L + 1, size=int(np.ceil(n / L)))
        bi = np.concatenate([ii[s:s + L] for s in starts])[:n]
        if len(set(y[bi])) < 2:
            continue
        out.append(roc_auc_score(y[bi], pa[bi]) - roc_auc_score(y[bi], pb[bi]))
    return np.percentile(out, [2.5, 97.5])


for target in ("massive", "kh_missile_day"):
    pb, y = oos(target, None); mb = ~np.isnan(pb)
    print(f"\n[{target}] baseline AUC {roc_auc_score(y[mb], pb[mb]):.3f} Brier {brier_score_loss(y[mb], pb[mb]):.3f} (n_test {mb.sum()}, positives {y[mb].sum()})")
    for sig in ("dip", "dip_share", "dip_day", "peace", "peace_share", "thr_ru"):
        pa, _ = oos(target, sig); m = mb & ~np.isnan(pa)
        lo, hi = block_ci(pa, pb, y, m)
        print(f"  +{sig:12s} AUC {roc_auc_score(y[m], pa[m]):.3f} (Δ {roc_auc_score(y[m], pa[m]) - roc_auc_score(y[m], pb[m]):+.3f} [{lo:+.3f},{hi:+.3f}])  Brier {brier_score_loss(y[m], pa[m]):.3f}")
