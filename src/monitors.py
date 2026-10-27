"""The monitoring channels as forecast features (oblast model v5, docs/forecast_v5.md).

The four monitors (history.MONITORS) give the v4/v5 label; before the cutoff they also carry the precursors of a strike
night: strategic aviation on the move, carriers at sea, warnings — and the explosions already reported that day. One
function, `daily()`, serves the history Parquet (hist5.py -> monitor_*_hist5) and the live posts in Neon (features.py).

  python src/monitors.py --cutoff 15 --tail 5     # last rows from the live posts (needs DATABASE_URL)
"""
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from explosions import decloak, is_report, regions_of  # noqa: E402
from features import MON_NAT as NAT  # noqa: E402
from history import MONITORS, lower  # noqa: E402

# last target day whose windows (cutoffs 15 and 21) lie entirely inside monitors/monitor_posts.parquet (ends 2026-10-23
# 14:20 UTC); later days come from the live posts in Neon
HIST_END = pd.Timestamp("2026-10-23")

# lowercase text after decloak(); Ukrainian and Russian spellings of the monitors
STRAT = re.compile(r"(ту-?\s?95|ту-?\s?160|ту-?\s?22|оленья|оленьї|олен'я|енгельс|энгельс|шайковк|дягилев|дягілев|моздок|саваслейк|"
                   r"міг-?\s?31|миг-?\s?31|кинджал|кинжал|стратегічн|стратегическ|бомбардувальник|бомбардировщик|ракетонос)")
SEA = re.compile(r"(калібр|калибр|носі[ії]в|носител|в мор[іе]\b|у мор[іе]\b|акваторі|фрегат|підводн|подводн|корвет|малий ракетний|каспі|каспий)")
WARN = re.compile(r"(масован|массирован|суттєв\w* загроз|существенн\w* угроз|попередж|предупрежд|висок\w* (ймовірн|ризик)|высок\w* (вероятн|риск)|"
                  r"комбінован|комбинирован|іскандер|искандер|балістик|баллистик|загроз\w* застосуван|угроз\w* применен)")


def window_day(ts_utc: pd.Series, cutoff_hour: int) -> pd.Series:
    """Target day D of a post: the 24 h window that ends at cutoff_hour (Kyiv) on D-1 (as features.rhetoric())."""
    ts = pd.to_datetime(ts_utc, utc=True).dt.tz_convert("Europe/Kyiv").dt.tz_localize(None)
    return (ts + pd.Timedelta(hours=24 - cutoff_hour)).dt.normalize() + pd.Timedelta(days=1)


def daily(posts: pd.DataFrame, cutoff_hour: int, regions) -> tuple[pd.DataFrame, pd.DataFrame]:
    """posts: id, ts_published (UTC), text -> (nat: d + NAT counts for every day of the range,
                                                reg: d, region_id, mon_reg = explosion reports naming the region).
    >>> p = pd.DataFrame({"id": list("abcd"), "ts_published": pd.to_datetime(["2026-10-22 08:00", "2026-10-22 09:00", "2026-10-22 18:00", "2026-10-23 07:00"], utc=True),
    ...     "text": ["Ту-95 злетіли з Оленьї", "💥 Луцьк - вибухи", "Носії Калібрів у морі", "Загроза масованого удару"]})
    >>> n, r = daily(p, 15, ["volynska", "lvivska"])
    >>> n[["d", "mon_posts", "mon_strat", "mon_sea", "mon_warn"]].values.tolist()
    [[Timestamp('2026-10-23 00:00:00'), 2, 1, 0, 0], [Timestamp('2026-10-24 00:00:00'), 2, 0, 1, 1]]
    >>> r[r.mon_reg > 0][["d", "region_id", "mon_reg"]].values.tolist()
    [[Timestamp('2026-10-23 00:00:00'), 'volynska', 1]]
    """
    p = posts.drop_duplicates("id").copy()
    p["d"] = window_day(p.ts_published, cutoff_hour)
    x = p.text.fillna("").map(lambda s: lower(decloak(s)))
    flags = pd.DataFrame({"mon_posts": 1, "mon_strat": x.map(lambda s: STRAT.search(s) is not None).astype(int),
                          "mon_sea": x.map(lambda s: SEA.search(s) is not None).astype(int),
                          "mon_warn": x.map(lambda s: WARN.search(s) is not None).astype(int)}, index=p.index)
    nat = flags.groupby(p.d).sum()
    days = pd.date_range(nat.index.min(), nat.index.max(), freq="D") if len(nat) else pd.DatetimeIndex([])
    nat = nat.reindex(days, fill_value=0).rename_axis("d").reset_index()
    rep = p[p.text.fillna("").map(is_report)].copy()
    rep["region_id"] = regions_of(rep.text) if len(rep) else []
    rep = rep.explode("region_id").dropna(subset=["region_id"])
    cnt = rep.groupby(["region_id", "d"]).size().rename("mon_reg").reset_index()
    grid = pd.MultiIndex.from_product([sorted(regions), days], names=["region_id", "d"]).to_frame(index=False)
    reg = grid.merge(cnt, on=["region_id", "d"], how="left").fillna({"mon_reg": 0})
    reg["mon_reg"] = reg.mon_reg.astype(int)
    return nat[["d"] + NAT], reg[["d", "region_id", "mon_reg"]]


def live_posts(conn, since) -> pd.DataFrame:
    """Monitor posts in Neon from `since` (UTC date) on."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, ts_published, text FROM messages WHERE source_id = ANY(%s) AND ts_published >= %s",
                    (["tg:" + c for c in MONITORS], pd.Timestamp(since).to_pydatetime()))
        df = pd.DataFrame(cur.fetchall(), columns=["id", "ts_published", "text"])
    df["ts_published"] = pd.to_datetime(df.ts_published, utc=True)
    return df


if __name__ == "__main__":
    import argparse
    from load_to_db import connect
    from oblasts import PANEL_REGIONS
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", type=int, default=15)
    ap.add_argument("--tail", type=int, default=10)
    a = ap.parse_args()
    with connect() as conn:
        n, r = daily(live_posts(conn, HIST_END - pd.Timedelta(days=2)), a.cutoff, PANEL_REGIONS)
    print(n.tail(a.tail).to_string())
    print(r[r.mon_reg > 0].tail(a.tail).to_string())
