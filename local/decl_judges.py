"""NAZK declarations of the judges the graph knows, fetched ahead of time on the VPS (neo4graph-decl.timer, nightly).

Judges as decisions name them («Біцюк А. В.», court_decisions.judge): the anti-corruption court, appeal courts and the
Supreme Court first, then by number of decisions. Each one goes through ua_worker.declarations() with the hint «суд»
(surname + initials + a court as the place of work); the list and the contents land in the site as for an opened person.
Done names go to decl_scan (migration 036) and are re-checked after 30 days.

  python local/decl_judges.py --max-seconds 14400 [--limit N]
"""
import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
import ua_worker as w  # noqa: E402

TOP = r"антикорупц|апеляц|Верховн|касаційн"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-seconds", type=int, default=4 * 3600)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    from load_to_db import connect
    env = w.cfg()
    deadline = time.time() + a.max_seconds
    conn = connect()
    rows = conn.execute(f"""SELECT j.judge, j.court FROM (
          SELECT judge, mode() WITHIN GROUP (ORDER BY court) court, count(*) n FROM court_decisions
          WHERE judge ~ '^\\S+ \\S\\. ?(\\S\\.)?$' GROUP BY judge) j
        LEFT JOIN decl_scan s ON s.name = j.judge AND s.done_at > now() - interval '30 days'
        WHERE s.name IS NULL ORDER BY (j.court ~* %s) DESC, j.n DESC""", (TOP,)).fetchall()
    conn.close()
    if a.limit:
        rows = rows[:a.limit]
    print(f"judges to scan: {len(rows)}")
    done = found = 0
    for judge, court in rows:
        if time.time() > deadline:
            print("time budget spent"); break
        hits, notes = [], []
        try:
            w.declarations(env, judge, "person", hits, notes, lambda: time.time() < deadline, "суд")
            for i in range(0, len(hits), 50):
                w.ingest(env, {"kind": "hits", "rows": hits[i:i + 50]})
        except Exception as e:  # noqa: BLE001
            notes.append(f"error: {e}"[:200])
        n = len({h["ref"] for h in hits})
        with connect() as c:
            c.execute("INSERT INTO decl_scan(name, court, found, note) VALUES (%s, %s, %s, %s) ON CONFLICT (name) DO UPDATE SET court = EXCLUDED.court, "
                      "found = EXCLUDED.found, note = EXCLUDED.note, done_at = now()", (judge, court, n, "; ".join(notes)[:500]))
        done += 1; found += bool(n)
        print(f"{judge} ({court}): {'; '.join(notes)}")
        time.sleep(1)
    print(f"scanned {done}, with declarations {found}")


if __name__ == "__main__":
    main()
