"""Archive the raw LLM label JSON out of Neon (migration 027).

message_labels.raw (entities, case block, narrative label and copies of the messages columns) is read by nothing in the
database once topics has its own column, yet it was ~60 MB of the 1 GB Neon free tier. Rows older than --days move to a
Parquet part file in the release "labels-archive"; only after the upload is confirmed is raw set to NULL in Neon.

  python src/labels_archive.py export [--days 14] [--out data/archive]   # -> data/archive/message_labels_<stamp>.parquet
  python src/labels_archive.py prune data/archive/message_labels_<stamp>.parquet   # after `gh release upload` succeeded
  python src/labels_archive.py find <message_id> [--dir data/archive]   # read a row back from local part files

Each part file holds (message_id, prompt_version, model, labeled_at, raw as JSON text). A re-labeled message gets a fresh raw
in Neon and lands in a later part again; the newest part wins.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from load_to_db import connect  # noqa: E402

RELEASE = "labels-archive"


def export(a):
    import pyarrow as pa
    import pyarrow.parquet as pq
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT message_id, prompt_version, model, labeled_at, raw::text FROM message_labels "
                    "WHERE raw IS NOT NULL AND labeled_at < now() - make_interval(days => %s) ORDER BY message_id", (a.days,))
        rows = cur.fetchall()
    if not rows:
        print("nothing to archive")
        return
    cols = list(zip(*rows))
    t = pa.table({"message_id": pa.array(cols[0], pa.string()), "prompt_version": pa.array(cols[1], pa.string()),
                  "model": pa.array(cols[2], pa.string()), "labeled_at": pa.array(cols[3], pa.timestamp("us", tz="UTC")),
                  "raw": pa.array(cols[4], pa.string())})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"message_labels_{time.strftime('%Y%m%d%H%M', time.gmtime())}.parquet"
    pq.write_table(t, f, compression="zstd", row_group_size=20000)
    back = pq.read_table(f)
    assert back.num_rows == len(rows) and back.column("message_id").to_pylist() == list(cols[0]), "read-back mismatch"
    print(f"{f}: {len(rows)} rows, {f.stat().st_size / 1e6:.1f} MB")
    if a.github_output:
        with open(a.github_output, "a") as g:
            g.write(f"file={f}\n")


def uploaded(name):
    """Size of the asset in the release, or None (needs gh with repo access)."""
    r = subprocess.run(["gh", "release", "view", RELEASE, "--json", "assets"], capture_output=True, text=True)
    if r.returncode:
        return None
    for asset in json.loads(r.stdout)["assets"]:
        if asset["name"] == name:
            return asset["size"]
    return None


def prune(a):
    import pyarrow.parquet as pq
    f = Path(a.file)
    size = uploaded(f.name)
    if size != f.stat().st_size:
        sys.exit(f"{f.name} is not in release {RELEASE} with the same size (got {size}, local {f.stat().st_size}): not pruning")
    ids = pq.read_table(f, columns=["message_id", "labeled_at"]).to_pylist()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE ar (message_id text, labeled_at timestamptz) ON COMMIT DROP")
        with cur.copy("COPY ar FROM STDIN") as cp:
            for r in ids:
                cp.write_row((r["message_id"], r["labeled_at"]))
        # only rows not re-labeled since the export (same labeled_at) lose their raw
        cur.execute("UPDATE message_labels l SET topics = coalesce(l.topics, CASE WHEN jsonb_typeof(l.raw->'topics') = 'array' THEN l.raw->'topics' END), "
                    "raw = NULL, archived_at = now() FROM ar WHERE l.message_id = ar.message_id "
                    "AND l.labeled_at = ar.labeled_at AND l.raw IS NOT NULL")
        print(f"pruned raw of {cur.rowcount} rows (archived in {RELEASE}/{f.name})")


def find(a):
    import pyarrow.parquet as pq
    hits = []
    for f in sorted(Path(a.dir).glob("message_labels_*.parquet")):
        t = pq.read_table(f, filters=[("message_id", "=", a.message_id)])
        hits += [(f.name, r) for r in t.to_pylist()]
    if not hits:
        sys.exit("not found (download the parts: gh release download labels-archive -D data/archive)")
    name, r = hits[-1]
    print(name, r["labeled_at"], r["prompt_version"], r["model"])
    print(json.dumps(json.loads(r["raw"]), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--days", type=int, default=14)
    e.add_argument("--out", default=str(ROOT / "data" / "archive"))
    e.add_argument("--github-output", default=None, help="append file=<path> here (GitHub Actions step output)")
    p = sub.add_parser("prune")
    p.add_argument("file")
    q = sub.add_parser("find")
    q.add_argument("message_id")
    q.add_argument("--dir", default=str(ROOT / "data" / "archive"))
    a = ap.parse_args()
    {"export": export, "prune": prune, "find": find}[a.cmd](a)
