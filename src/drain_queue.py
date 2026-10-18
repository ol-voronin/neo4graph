"""Run queued registry chains (query_queue, filled by the web UI). Each item: mark running -> resolve_query -> done/failed."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=3)
    a = ap.parse_args()
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS query_queue (id bigserial PRIMARY KEY, query text NOT NULL, kind text, status text NOT NULL DEFAULT 'queued', "
                "created_at timestamptz DEFAULT now(), started_at timestamptz, finished_at timestamptz, note text)")
    conn.commit()
    cur.execute("SELECT id, query, kind FROM query_queue WHERE status='queued' ORDER BY created_at LIMIT %s", (a.max,))
    items = cur.fetchall()
    print(f"queued: {len(items)}")
    for qid, query, kind in items:
        cur.execute("UPDATE query_queue SET status='running', started_at=now() WHERE id=%s", (qid,))
        conn.commit()
        yr = "20" + query.split("/")[2][:2] if kind == "case" and query.count("/") == 2 else "2021"
        if kind == "category_index":  # "2025|земел" or "2025|земел|Вирок"
            parts = query.split("|")
            cmd = [sys.executable, "src/edrsr_index.py", "--years", parts[0], "--category", parts[1]] + (["--form", parts[2]] if len(parts) > 2 and parts[2] else []) + ["--texts", "0"]
        elif kind == "web":
            cmd = [sys.executable, "src/web_mentions.py", query]
        elif kind == "url":  # "https://... | 932/2641/25" optional case after a pipe
            parts = [x.strip() for x in query.split("|")]
            cmd = [sys.executable, "src/ingest_url.py", parts[0]] + (["--cause", parts[1]] if len(parts) > 1 and parts[1] else [])
        else:
            cmd = [sys.executable, "src/resolve_query.py", query, "--years", f"{yr}-2026"] + (["--kind", kind] if kind else [])
        print("run:", " ".join(cmd))
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        tail = (r.stdout + r.stderr)[-1500:]
        print(tail)
        cur.execute("UPDATE query_queue SET status=%s, finished_at=now(), note=%s WHERE id=%s", ("done" if r.returncode == 0 else "failed", tail[-600:], qid))
        conn.commit()


if __name__ == "__main__":
    main()
