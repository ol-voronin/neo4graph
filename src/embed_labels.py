"""Finish the LLM labeling for free: embed every rule-flagged panel post with an open multilingual model, train a small
classifier on the posts Haiku already labeled (5.8k), and predict the labels for the rest (~21k).

Labels learned (from message_labels.raw, prompt v2):
  t2  threat_level >= 2            l2  t2 and (target or timeframe mentioned) and not scheduled context
  t3  threat_level >= 3            rh  event_kind == rhetoric          op  event_kind == operational
Output:
  table message_labels_pred(message_id, model, y_* from the LLM where it exists, p_* predicted — out-of-fold for
  labeled posts, so every post has an honest probability) and data/llm_daily_full.csv in the llm_daily.csv layout
  (test3 reads it) using the expected value sum(p) per day/side.

  EMB_MODEL=intfloat/multilingual-e5-small python src/embed_labels.py [--no-write]
Embeddings are cached in data/emb/<model>.npz (gitignored; the workflow caches it between runs).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from label_messages import PANEL_SQL, SKIP_SOURCES, SKIP_TYPES  # noqa: E402
from load_to_db import connect  # noqa: E402

MODEL = os.environ.get("EMB_MODEL", "intfloat/multilingual-e5-small")
TARGETS = ["t2", "l2", "t3", "rh", "op"]


def load_posts(cur):
    cur.execute(
        "SELECT m.id, m.text, m.ts_published, s.country, l.raw FROM messages m JOIN sources s ON s.id=m.source_id "
        "LEFT JOIN message_labels l ON l.message_id=m.id "
        "WHERE s.id = ANY(%s) AND m.ts_published >= '2026-03-31' AND m.id IN (SELECT message_id FROM rule_features WHERE r_threat) "
        "AND length(m.text) >= 40 AND s.id <> ALL(%s) AND s.type <> ALL(%s) ORDER BY m.id" % (PANEL_SQL, "%s", "%s"),
        (list(SKIP_SOURCES), list(SKIP_TYPES)))
    rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["id", "text", "ts", "country", "raw"])
    df["date"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert("Europe/Kiev").dt.date.astype(str)
    df["side"] = np.where(df.country == "RU", "ru", "ua")
    return df


def targets_from_raw(raw):
    if raw is None:
        return [np.nan] * len(TARGETS)
    r = raw if isinstance(raw, dict) else json.loads(raw)
    tl = r.get("threat_level") or 0
    t2 = int(tl >= 2)
    l2 = int(t2 and (r.get("target_mentioned") or r.get("timeframe_mentioned")) and not r.get("is_scheduled_context"))
    return [t2, l2, int(tl >= 3), int(r.get("event_kind") == "rhetoric"), int(r.get("event_kind") == "operational")]


def embed(df):
    cache = ROOT / "data" / "emb" / (MODEL.replace("/", "__") + ".npz")
    cache.parent.mkdir(parents=True, exist_ok=True)
    have = {}
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        have = dict(zip(z["ids"].tolist(), z["X"]))
        print(f"cache: {len(have)} embeddings")
    todo = df[~df.id.isin(have.keys())]
    if len(todo):
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(MODEL, device="cpu")
        m.max_seq_length = 512
        prefix = "query: " if "e5" in MODEL else ""
        t0 = time.time()
        X = m.encode([prefix + t[:3000] for t in todo.text.tolist()], batch_size=64, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
        print(f"embedded {len(todo)} posts in {time.time() - t0:.0f}s ({X.shape[1]} dims)")
        have.update(zip(todo.id.tolist(), X.astype(np.float32)))
        ids = np.array(list(have.keys()))
        np.savez_compressed(cache, ids=ids, X=np.stack([have[i] for i in ids]))
    return np.stack([have[i] for i in df.id.tolist()])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    conn = connect()
    cur = conn.cursor()
    df = load_posts(cur)
    conn.close()  # embedding can take an hour; Neon kills idle transactions — reconnect for the write
    Y = pd.DataFrame([targets_from_raw(r) for r in df.raw], columns=TARGETS)
    lab = Y.t2.notna().values
    print(f"panel posts {len(df)}, labeled {lab.sum()}, by side: {df.side.value_counts().to_dict()}")
    X = embed(df)
    P = pd.DataFrame(index=df.index, columns=TARGETS, dtype=float)
    skf = StratifiedKFold(5, shuffle=True, random_state=0)
    for t in TARGETS:
        y = Y[t].values[lab].astype(int)
        if y.sum() < 20:
            print(f"{t}: only {y.sum()} positives, skipped"); continue
        clf = LogisticRegression(C=2.0, max_iter=2000, class_weight="balanced")
        oof = cross_val_predict(clf, X[lab], y, cv=skf, method="predict_proba")[:, 1]
        print(f"{t}: pos {y.mean():.2f}  OOF AUC {roc_auc_score(y, oof):.3f}  F1@0.5 {f1_score(y, oof >= 0.5):.3f}  (n={len(y)})")
        P.loc[lab, t] = oof
        P.loc[~lab, t] = clf.fit(X[lab], y).predict_proba(X[~lab])[:, 1]
    # daily aggregation in the llm_daily.csv layout: counts = expected values from probabilities (OOF for labeled)
    out = []
    for d, g in df.assign(**{f"p_{t}": P[t] for t in TARGETS}).groupby("date"):
        row = {"date": d}
        for side in ("ru", "ua"):
            s = g[g.side == side]
            row[f"n_{side}"] = len(s)
            for t in TARGETS:
                row[f"{t}_{side}"] = round(float(s[f"p_{t}"].sum()), 2)
        out.append(row)
    daily = pd.DataFrame(out)
    daily.to_csv(ROOT / "data" / "llm_daily_full.csv", index=False)
    print(daily.tail(3).to_string())
    if a.no_write:
        return
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS message_labels_pred (message_id bigint PRIMARY KEY, model text, " +
                ", ".join(f"y_{t} smallint" for t in TARGETS) + ", " + ", ".join(f"p_{t} real" for t in TARGETS) + ", pred_at timestamptz DEFAULT now())")
    rows = []
    for i, r in df.iterrows():
        ys = [None if pd.isna(v) else int(v) for v in Y.loc[i]]
        ps = [None if pd.isna(v) else float(v) for v in P.loc[i]]
        rows.append((int(r.id), MODEL, *ys, *ps))
    cur.executemany("INSERT INTO message_labels_pred(message_id, model, " + ", ".join(f"y_{t}" for t in TARGETS) + ", " + ", ".join(f"p_{t}" for t in TARGETS) +
                    ") VALUES (" + ",".join(["%s"] * (2 + 2 * len(TARGETS))) + ") ON CONFLICT (message_id) DO UPDATE SET model=EXCLUDED.model, " +
                    ", ".join(f"y_{t}=EXCLUDED.y_{t}, p_{t}=EXCLUDED.p_{t}" for t in TARGETS) + ", pred_at=now()", rows)
    conn.commit()
    print(f"wrote {len(rows)} rows to message_labels_pred")


if __name__ == "__main__":
    main()
