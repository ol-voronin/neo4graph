"""Query-driven resolution chain. Given a query, classify it and run every applicable registry step, writing results
into Postgres so the graph page can show the object with all its links.

  python src/resolve_query.py "642/8729/21"            # case number -> ЄДРСР (all years) -> texts -> entities -> ЄДР
  python src/resolve_query.py "Кацуба Александр"       # person -> corpus + NAZK corrupt register + ЄДР founders/signers + ЄДРСР
  python src/resolve_query.py "ТОВ Газ-мдс" | 24253556 # company -> ЄДР -> founders -> ЄДРСР -> corpus

Steps call the existing tools (edrsr_index.py, edr_match.py, extract_entities.py) as subprocesses so each stays
independently testable. Every step records what it did in query_log.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
CAUSE_RE = re.compile(r"^\d{1,4}/\d{1,6}/\d{2}(?:-[\w/]+)?$")
EDRPOU_RE = re.compile(r"^\d{8}$")
ORG_RE = re.compile(r"^(тов|пат|прат|ат|тдв|дп|кп|пп|фг|го|бф|ооо|зао|оао)\b|\bltd\b|\bllc\b|\bінк\b|\binc\b", re.I)
NAME_RE = re.compile(r"^[А-ЯІЇЄҐA-Z][\w'’-]+(\s+[А-ЯІЇЄҐA-Z][\w'’-]+){1,2}$")


def classify(q: str) -> str:
    q = q.strip()
    if CAUSE_RE.match(q):
        return "case"
    if EDRPOU_RE.match(q):
        return "edrpou"
    if ORG_RE.search(q) or re.search(r"[«»\"]", q):
        return "org"
    if NAME_RE.match(q):
        return "person"
    return "text"


def run(cmd, log, step):
    t0 = time.time()
    print(f"\n== {step}: {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout + r.stderr)[-4000:]
    print(out)
    log.append({"step": step, "cmd": " ".join(cmd), "rc": r.returncode, "secs": round(time.time() - t0, 1), "tail": out[-600:]})
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--years", default="2021-2026")
    ap.add_argument("--texts", type=int, default=30)
    ap.add_argument("--kind", default=None, help="override classification: case|edrpou|org|person|text")
    a = ap.parse_args()
    q = a.query.strip()
    kind = a.kind or classify(q)
    print(f"query: {q!r} -> {kind}")
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS query_log (id bigserial PRIMARY KEY, query text, kind text, steps jsonb, created_at timestamptz DEFAULT now())")
    conn.commit()
    log = []
    py = sys.executable
    if kind == "case":
        run([py, "src/edrsr_index.py", "--years", a.years, "--cause", q, "--texts", str(a.texts)], log, "ЄДРСР: decisions for the case")
        run([py, "src/extract_court_texts.py", "--cause", q], log, "LLM: entities from decision texts")
        run([py, "src/edr_match.py", "--download", "--from-db"], log, "ЄДР: companies named in the case")
    elif kind in ("org", "edrpou"):
        names = q if kind == "org" else ""
        run([py, "src/edr_match.py", "--download", "--from-db", "--names", names] + (["--edrpou", q] if kind == "edrpou" else []), log, "ЄДР: company record, founders, signers")
        run([py, "src/edrsr_index.py", "--years", a.years, "--from-db", "--texts", str(a.texts)], log, "ЄДРСР: cases referencing it")
    elif kind == "person":
        run([py, "src/corpus_mentions.py", q], log, "corpus: posts and statements mentioning the name")
        run([py, "src/nazk_corrupt.py", "--name", q], log, "НАЗК: register of corruption offenders")
        run([py, "src/edr_match.py", "--download", "--person", q], log, "ЄДР: companies where the person is founder/signer")
        run([py, "src/edrsr_index.py", "--years", a.years, "--judge", q, "--texts", str(a.texts)], log, "ЄДРСР: decisions by/with the name")
    else:
        run([py, "src/corpus_mentions.py", q], log, "corpus: full-text search")
    cur.execute("INSERT INTO query_log(query, kind, steps) VALUES (%s,%s,%s)", (q, kind, json.dumps(log, ensure_ascii=False)))
    conn.commit()
    print("\nsummary:", json.dumps([{k: s[k] for k in ("step", "rc", "secs")} for s in log], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
