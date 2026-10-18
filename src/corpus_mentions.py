"""Full-text mentions of a query across posts, statements and extracted entities; stores hits in query_hits so the
graph can link the object to the posts that mention it.  python src/corpus_mentions.py "Кацуба"
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    q = " ".join(sys.argv[1:]).strip()
    if len(q) < 3:
        sys.exit("query too short")
    from load_to_db import connect
    conn = connect()
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS query_hits (query text, message_id text REFERENCES messages(id) ON DELETE CASCADE, "
                "where_found text, created_at timestamptz DEFAULT now(), PRIMARY KEY (query, message_id, where_found))")
    # surname-only pattern for Cyrillic names so inflected forms match (Кацуба / Кацуби / Кацубу)
    parts = q.split()
    stem = parts[0][:-1] if re.match(r"^[А-ЯІЇЄҐа-яіїєґ'’-]+$", parts[0]) and len(parts[0]) > 5 else parts[0]
    pat = re.escape(stem)
    cur.execute("INSERT INTO query_hits(query, message_id, where_found) SELECT %s, id, 'text' FROM messages WHERE text ~* %s "
                "ON CONFLICT DO NOTHING", (q, pat))
    n1 = cur.rowcount
    cur.execute("INSERT INTO query_hits(query, message_id, where_found) SELECT %s, message_id, 'statement' FROM statements "
                "WHERE quote ~* %s OR actor_name ~* %s ON CONFLICT DO NOTHING", (q, pat, pat))
    n2 = cur.rowcount
    cur.execute("INSERT INTO query_hits(query, message_id, where_found) SELECT %s, message_id, 'entity' FROM ee_persons "
                "WHERE name_norm ~* %s OR name_as_written ~* %s ON CONFLICT DO NOTHING", (q, pat, pat))
    n3 = cur.rowcount
    conn.commit()
    cur.execute("SELECT count(*), min(m.ts_published)::date, max(m.ts_published)::date FROM query_hits h JOIN messages m ON m.id=h.message_id WHERE h.query=%s", (q,))
    tot, d0, d1 = cur.fetchone()
    print(f"mentions for {q!r} (stem {stem!r}): posts {n1}, statements {n2}, entities {n3}; total {tot}, {d0}..{d1}")


if __name__ == "__main__":
    main()
