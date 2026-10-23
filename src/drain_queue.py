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
    # a job killed by its timeout leaves its item 'running' forever: give those back after 3 h (once; then failed)
    cur.execute("UPDATE query_queue SET status = CASE WHEN note LIKE 'requeued:%%' THEN 'failed' ELSE 'queued' END, "
                "note = 'requeued: the run that took it did not finish' WHERE status = 'running' AND started_at < now() - interval '3 hours'")
    conn.commit()
    items = []
    for _ in range(a.max):
        # claim one item atomically: queue.yml and the live loop (live.yml) may drain at the same time
        cur.execute("UPDATE query_queue SET status='running', started_at=now() WHERE id = (SELECT id FROM query_queue WHERE status='queued' "
                    "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id, query, kind")
        row = cur.fetchone()
        conn.commit()
        if not row:
            break
        items.append(row)
        qid, query, kind = row
        yr = "20" + query.split("/")[2][:2] if kind == "case" and query.count("/") == 2 else "2021"
        if kind == "category_index":  # "2025|земел" or "2025|земел|Вирок"
            parts = query.split("|")
            cmd = [sys.executable, "src/edrsr_index.py", "--years", parts[0], "--category", parts[1]] + (["--form", parts[2]] if len(parts) > 2 and parts[2] else []) + ["--texts", "0"]
        elif kind == "fop":  # ЄДР ФОП card only (chat "знайди ФОП …"), no registry chain
            cmd = [sys.executable, "src/fop_lookup.py", query]
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
        try:  # chains run for minutes; the idle connection is often dropped by then
            cur.execute("SELECT 1")
        except Exception:  # noqa: BLE001
            conn = connect()
            cur = conn.cursor()
        cur.execute("UPDATE query_queue SET status=%s, finished_at=now(), note=%s WHERE id=%s", ("done" if r.returncode == 0 else "failed", tail[-600:], qid))
        conn.commit()
    print(f"queued: {len(items)}")  # queue.yml greps this to refresh the graph snapshot
    if items:
        from load_to_db import refresh_search_names
        refresh_search_names(conn)


if __name__ == "__main__":
    main()
