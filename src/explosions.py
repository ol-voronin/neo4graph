"""Explosion reports of the monitoring channels -> daily per-oblast labels (migration 030), the "were there explosions in my
oblast" ground truth that the massed-strike labels cannot give for the rear (one Lviv explosion is not >= 7 missiles).

Sources: MONITORS (src/history.py): history Parquet (release history-latest, monitors/monitor_posts.parquet) before the
live range + their posts in Neon (messages, source type osint). Rules:
  a report   = a post that says explosions happened (вибух / взрыв / 💥) and names an oblast (geo_regions(), the same
               generated regex as the SQL function), not a negation («без вибухів», «вибухів не було», «отбой»)
  explosion day (region, Kyiv day D) = >= 1 report naming the region
  known negative = no report, while the feed was alive that day (>= ALIVE_POSTS posts of the monitors) — the same idea as
               the alert-free negatives of ob_massive2; occupied oblasts (Luhansk, Crimea) get no negatives

  python src/explosions.py build [--dry-run]      # -> explosion_daily
  python src/explosions.py sample --region lvivska --day 2026-10-23
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from history import MONITORS, SETS, lower  # noqa: E402

ALIVE_POSTS = 30
NO_NEG = {"luhanska", "crimea"}
BOOM = re.compile(r"(вибух|взрыв|💥|прил[её]т|детонац)")
NEG = re.compile(r"(без вибух|вибухів не|взрывов не|без взрыв|відбій|отбой|не чути вибух|информация о взрывах не подтвер|фейк)")


def is_report(text: str) -> bool:
    """A post that says explosions happened.
    >>> is_report("💥 Ковель (Волинь) - вибухи"), is_report("Відбій тривоги у Львові"), is_report("Львів - без вибухів")
    (True, False, False)
    """
    x = lower(decloak(text or ""))
    return bool(BOOM.search(x)) and not NEG.search(x)


# monitors mix Latin look-alikes into Cyrillic words («Xмельниччинa», «Kиїв», «Сyмщина»), which no gazetteer form matches
HOMO = str.maketrans("aceiopxykKXHBMTACEIOPY", "асеіорхукКХНВМТАСЕІОРУ")
MIXED = re.compile(r"\w*[а-яіїєґё]\w*", re.I)


def decloak(s: str) -> str:
    """Latin look-alikes -> Cyrillic inside words that already have Cyrillic letters (Latin words stay).
    >>> decloak("💥Xмельниччинa - вибухи (fpv)")
    '💥Хмельниччина - вибухи (fpv)'
    """
    return MIXED.sub(lambda m: m.group(0).translate(HOMO), s)


# monitor-only spellings the gazetteer does not have: raions named after the oblast centre, «Вінничина» with one ч.
# Not in geo_regions(): that SQL function feeds frozen models and must not move.
EXTRA = {"vinnytska": r"вінничин|вінницьк\w* р-н", "rivnenska": r"рівненськ\w* р-н", "zhytomyrska": r"житомирськ\w* р-н",
         "chernihivska": r"чернігівськ\w* р-н", "kyivska": r"(бучанськ|броварськ|бориспільськ|білоцерківськ|вишгородськ|обухівськ|фастівськ)\w* р-н",
         "poltavska": r"полтавськ\w* р-н", "sumska": r"сумськ\w* р-н", "kharkivska": r"харківськ\w* р-н", "odeska": r"одеськ\w* р-н",
         "dnipropetrovska": r"дніпровськ\w* р-н", "zaporizka": r"запорізьк\w* р-н|зароріжж", "khmelnytska": r"хмельницьк\w* р-н",
         "cherkaska": r"черкаськ\w* р-н", "kirovohradska": r"кропивницьк\w* р-н", "volynska": r"луцьк\w* р-н", "lvivska": r"львівськ\w* р-н",
         "ternopilska": r"тернопільськ\w* р-н", "ivano-frankivska": r"івано-франківськ\w* р-н", "zakarpatska": r"ужгородськ\w* р-н",
         "chernivetska": r"чернівецьк\w* р-н", "khersonska": r"херсонськ\w* р-н"}


def regions_of(texts: pd.Series) -> pd.Series:
    """Oblast slugs named in each text (Python twin of geo_regions(), after decloak())."""
    from build_gazetteer import region_patterns, sql_regex
    pats = {r: re.compile(sql_regex(a)) for r, a in region_patterns().items()}
    norm = texts.map(lambda s: lower(decloak(s or "")).replace("’", "'").replace("ʼ", "'").replace("`", "'").replace("‘", "'"))
    extra = {r: re.compile(v) for r, v in EXTRA.items()}
    return norm.map(lambda x: sorted({r for r, rx in pats.items() if rx.search(x)} | {r for r, rx in extra.items() if rx.search(x)}))


def daily(posts: pd.DataFrame, regions) -> pd.DataFrame:
    """posts: id, source_id, ts_published (UTC), text -> (region_id, d, reports, known, expl).
    >>> p = pd.DataFrame({"id": list("abc"), "source_id": ["tg:war_monitor"] * 3,
    ...     "ts_published": pd.to_datetime(["2026-10-23 05:00", "2026-10-23 06:00", "2026-10-23 07:00"], utc=True),
    ...     "text": ["💥 Луцьк - вибухи", "Відбій тривоги у Луцьку", "Загроза БпЛА для Львівщини"]})
    >>> import explosions; explosions.ALIVE_POSTS = 2
    >>> daily(p, ["volynska", "lvivska", "luhanska"])[["region_id", "reports", "expl"]].values.tolist()
    [['luhanska', 0, nan], ['lvivska', 0, 0.0], ['volynska', 1, 1.0]]
    """
    p = posts.copy()
    p["d"] = pd.to_datetime(p.ts_published, utc=True).dt.tz_convert("Europe/Kyiv").dt.tz_localize(None).dt.normalize()
    alive = p.groupby("d").size()
    rep = p[p.text.map(is_report)].copy()
    rep["region_id"] = regions_of(rep.text)
    rep = rep.explode("region_id").dropna(subset=["region_id"])
    cnt = rep.groupby(["region_id", "d"]).id.nunique().rename("reports")
    days = pd.date_range(p.d.min(), p.d.max(), freq="D")
    grid = pd.MultiIndex.from_product([sorted(regions), days], names=["region_id", "d"]).to_frame(index=False)
    df = grid.merge(cnt.reset_index(), on=["region_id", "d"], how="left").fillna({"reports": 0})
    df["reports"] = df.reports.astype(int)
    df["posts"] = df.d.map(alive).fillna(0).astype(int)
    df["known"] = (df.reports > 0) | ((df.posts >= ALIVE_POSTS) & ~df.region_id.isin(NO_NEG))
    df["expl"] = df.reports.gt(0).astype(float).where(df.known)
    return df


def load_posts(conn):
    out, parquet = SETS["monitors"]
    hist = pd.read_parquet(parquet, columns=["id", "source_id", "ts_published", "text"]) if parquet.exists() else pd.DataFrame(columns=["id", "source_id", "ts_published", "text"])
    with conn.cursor() as cur:
        cur.execute("SELECT id, source_id, ts_published, text FROM messages WHERE source_id = ANY(%s)", (["tg:" + c for c in MONITORS],))
        live = pd.DataFrame(cur.fetchall(), columns=["id", "source_id", "ts_published", "text"])
    for f in (hist, live):
        f["ts_published"] = pd.to_datetime(f.ts_published, utc=True)
    return pd.concat([hist, live]).drop_duplicates("id")


def cmd_build(a):
    from load_to_db import connect
    from oblasts import PANEL_REGIONS
    with connect() as conn:
        posts = load_posts(conn)
        print(f"monitor posts {len(posts)} ({posts.ts_published.min()} .. {posts.ts_published.max()})")
        df = daily(posts, PANEL_REGIONS)
        k = df[df.known]
        print(f"rows {len(df)}, known {len(k)}, explosion days {int(k.expl.sum())}; by region (share of known days with explosions):")
        print(k.groupby("region_id").expl.mean().round(3).sort_values().to_string())
        if a.dry_run:
            return
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE ed (LIKE explosion_daily INCLUDING DEFAULTS) ON COMMIT DROP")
            with cur.copy("COPY ed (region_id, d, reports, posts, known) FROM STDIN") as cp:
                for r in df.itertuples(index=False):
                    cp.write_row((r.region_id, r.d.date(), int(r.reports), int(r.posts), bool(r.known)))
            cur.execute("DELETE FROM explosion_daily WHERE d >= (SELECT min(d) FROM ed)")
            cur.execute("INSERT INTO explosion_daily SELECT * FROM ed")
            print("explosion_daily rows", cur.rowcount)
        conn.commit()


def cmd_sample(a):
    from load_to_db import connect
    with connect() as conn:
        p = load_posts(conn)
    p["d"] = p.ts_published.dt.tz_convert("Europe/Kyiv").dt.date.astype(str)
    p = p[(p.d == a.day) & p.text.map(is_report)]
    p["r"] = regions_of(p.text)
    for r in p[p.r.map(lambda x: a.region in x)].itertuples():
        print(r.ts_published.tz_convert("Europe/Kyiv").strftime("%H:%M"), r.source_id, re.sub(r"\s+", " ", r.text)[:120])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--dry-run", action="store_true")
    b.set_defaults(fn=cmd_build)
    s = sub.add_parser("sample")
    s.add_argument("--region", required=True)
    s.add_argument("--day", required=True)
    s.set_defaults(fn=cmd_sample)
    a = ap.parse_args()
    a.fn(a)
