"""Daily strike summaries of the oblast administrations (OVA) -> one row per oblast and day (`oblast_daily`).

Generalises parse_kharkiv_daily.py. The channels and their per-channel settings live in src/oblasts.py. Kharkiv keeps
its exact parser (the @synegubov weapon block); every other channel goes through generic regexes:

  summary detection  "минулої доби / протягом (упродовж) доби / за добу / оперативна інформація|ситуація /
                     за час повітряних тривог" or the channel's own `start` regex
  scope guard        the post must name its own oblast (or centre) and may name at most two other oblasts: OVA
                     channels repost the President's NATIONAL summaries ("16 балістичних ракет ... від Харківщини
                     до Волині"), whose numbers must not land in one oblast
                     weekly roundups ("за підсумками тижня") are skipped
  counts             "<N> ракет", "<N> БпЛА / дронів / шахедів" (FPV-dominated and "скиди з БпЛА" go to fpv),
                     "<N> КАБ / авіабомб / авіаудар(ів)", "<N> ... РСЗВ", "<N> артилерійськ...", "<N> ударів по <M>
                     населених пунктах", "знешкоджено/збито <N>", загинули/поранені
  date               covers="prev": the day before the post (Kyiv); "auto": the day before if posted before 12:00
                     Kyiv and the text says "минулої доби / протягом доби / за добу", else the posting day
Several summaries for the same oblast and day are merged with MAX per field (they repeat the same figures; summing
would double count). `complete` comes from the channel config: only a complete summary turns "no number" into zero.

Usage: python src/parse_oblast_daily.py [--since 2026-10-01]
Input : data/ova/<handle>*.jsonl and data/telegram/<handle>*.jsonl   Output: data/oblast_daily.jsonl
"""
import argparse
import glob
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_kharkiv_daily  # noqa: E402
from oblasts import OBLASTS, channel_config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001
    KYIV = timezone(timedelta(hours=3))

SUMMARY_RE = re.compile(r"(минул\w* доб|(протягом|упродовж|впродовж) (минулої )?доби|за (минулу )?добу|оперативн\w+ (інформаці|ситуаці)|"
                        r"за час повітрян\w+ тривог)", re.I)
PREV_RE = re.compile(r"(минул\w* доб|(протягом|упродовж|впродовж) (минулої )?доби|за (минулу )?добу|з 06:00)", re.I)
NUM = r"(?<![\d:.,])(\d{1,5})"          # not the tail of a time ("18:30") or a decimal
# up to three words between the number and the weapon ("5 БпЛА", "16 балістичних ракет", "21 авіаудар"), same clause,
# never "разів" ("понад 30 разів ворог атакував ... безпілотниками" is a count of attacks, not of drones)
GAP = r"(?:(?!раз)[^\s\d.;,:]+\s+){0,3}?"
MISSILE_RE = re.compile(NUM + r"\s+" + GAP + r"\b(ракет\w*|крилат\w*|балістичн\w*)", re.I)
DRONE_RE = re.compile(NUM + r"\s+" + GAP + r"(БпЛА|безпілотник\w*|дрон\w*|шахед\w*|«?герань)", re.I)
KAB_RE = re.compile(NUM + r"\s+" + GAP + r"(КАБ|авіабомб\w*|керован\w+ авіа\w*|авіаудар\w*|авіаційн\w+ удар\w*)", re.I)
MLRS_RE = re.compile(NUM + r"\s+" + GAP + r"(РСЗВ|реактивн\w+ систем\w+ залпового)", re.I)
ART_RE = re.compile(NUM + r"\s+" + GAP + r"(артилерійськ\w+|артобстріл\w*)", re.I)
ATTACKS_RE = re.compile(r"(?:понад |більше )?" + NUM + r"\s+(?:удар\w*|обстріл\w*|атак\w*|раз\w*)(?:[^\d\n]{0,30}?)\s+по\s+" + NUM + r"\s+населен", re.I)
ATTACKS2_RE = re.compile(r"(?:понад|більше)\s+" + NUM + r"\s+раз\w*\s+ворог\s+атакував", re.I)
SHOT_RE = re.compile(r"(?:знешкоджено|збито|знищено|сбито)\s+" + NUM + r"(?![\d-])", re.I)
PERSONS = r"(?![\d-])(?!\s*-?\s*річ)"   # "загинула 83-річна жінка" is an age, not a count
KILLED_RE = re.compile(r"(?:загинул\w*|загинуло|вбито)\s+" + NUM + PERSONS + r"|" + NUM + PERSONS + r"\s+(?:\w+\s+){0,2}(?:загинул\w*|загиблих)", re.I)
INJURED_RE = re.compile(r"(?:поранен\w*|постраждал\w*|травмован\w*)\s+" + NUM + PERSONS + r"|" + NUM + PERSONS + r"\s+(?:\w+\s+){0,2}(?:поранен\w*|постраждал\w*|травмован\w*)", re.I)
ATTACK_WORDS = re.compile(r"(атакував|атакувал|атак[иу]|удар|обстріл|влучан|вибух|пошкоджен|зруйнован|загинул|поранен|постраждал)", re.I)
FPV_CTX = re.compile(r"(fpv|скид)", re.I)   # FPV and drone-dropped munitions ("37 скидів з БпЛА") are front-line, not strike drones
WEEKLY_RE = re.compile(r"(підсумками (минулого |цього )?тижня|за (минулий |цей )?тиждень|протягом (минулого )?тижня)", re.I)


def own_patterns():
    """slug -> regex for 'this text is about my oblast' (adjective, '-щина' stem, centre), and one for any oblast."""
    pats = {}
    for slug, o in OBLASTS.items():
        alts = [re.escape(o["name"].lower()[:-2])]  # харківськ-
        if o["shchyna"]:
            alts.append(re.escape(o["shchyna"]))
        if o["center"]:
            alts.append(re.escape(o["center"].lower()[:max(4, len(o["center"]) - 1)]))
        pats[slug] = re.compile("(" + "|".join(alts) + ")", re.I)
    return pats


OWN = own_patterns()


def _sum(rx, text, group=1, skip_fpv=False):
    total, fpv = 0, 0
    for m in rx.finditer(text):
        g = next((x for x in m.groups() if x and x.isdigit()), None)
        if g is None:
            continue
        ctx = text[m.start():m.end() + 40]
        if skip_fpv and FPV_CTX.search(ctx):
            fpv += int(g)
        else:
            total += int(g)
    return total, fpv


def _first(rx, text):
    m = rx.search(text)
    if not m:
        return None
    g = next((x for x in m.groups() if x and x.isdigit()), None)
    return int(g) if g else None


def scope_ok(text: str, slug: str) -> bool:
    """Own oblast named, at most two other oblasts named (national reposts name many)."""
    if not OWN[slug].search(text):
        return False
    others = sum(1 for s, rx in OWN.items() if s != slug and s not in ("kyiv-city", "kyivska") and rx.search(text))
    return others <= 2


def parse_generic(text: str, slug: str, cfg: dict):
    start = re.compile(cfg["start"], re.I) if cfg.get("start") else SUMMARY_RE
    if not start.search(text) or not scope_ok(text, slug) or WEEKLY_RE.search(text):  # weekly roundups are not daily counts
        return None
    missiles, _ = _sum(MISSILE_RE, text)
    drones, fpv = _sum(DRONE_RE, text, skip_fpv=True)
    kab, _ = _sum(KAB_RE, text)
    mlrs, _ = _sum(MLRS_RE, text)
    art, _ = _sum(ART_RE, text)
    m = ATTACKS_RE.search(text)
    attacks_n = int(m.group(1)) if m else _first(ATTACKS2_RE, text)
    settlements_n = int(m.group(2)) if m else None
    any_count = missiles + drones + fpv + kab + mlrs + art + (attacks_n or 0)
    return {"missiles": missiles, "drones": drones, "fpv": fpv, "kab": kab, "mlrs": mlrs, "artillery": art,
            "attacks_n": attacks_n, "settlements_n": settlements_n, "shot_down": _first(SHOT_RE, text),
            "killed": _first(KILLED_RE, text), "injured": _first(INJURED_RE, text),
            "attacked": bool(any_count) or bool(ATTACK_WORDS.search(text)), "city_attacked": None}


def parse_kharkiv(text: str):
    r = parse_kharkiv_daily.parse(text)
    if not r:
        return None
    return {"missiles": r["missiles"], "drones": r["drones"], "fpv": r["fpv"], "kab": r["guided_bombs"], "mlrs": r["mlrs"],
            "artillery": None, "attacks_n": None, "settlements_n": r["settlements_n"], "shot_down": None,
            "killed": _first(KILLED_RE, text), "injured": _first(INJURED_RE, text),
            "attacked": bool(r["missiles"] or r["drones"] or r["guided_bombs"] or r["mlrs"] or r["fpv"] or r["settlements_n"] or r["city_attacked"]),
            "city_attacked": r["city_attacked"], "complete": r["has_weapon_block"]}


def day_covered(ts_iso: str, text: str, covers: str) -> str:
    ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(KYIV)
    prev = covers == "prev" or (covers == "auto" and ts.hour < 12 and PREV_RE.search(text))
    return (ts.date() - timedelta(days=1) if prev else ts.date()).isoformat()


def parse_post(m: dict, slug: str, cfg: dict):
    """One channel post -> oblast_daily row dict or None."""
    text = m.get("text") or ""
    if cfg["parser"] == "kharkiv":
        r = parse_kharkiv(text)
        covers = "prev"
    else:
        r = parse_generic(text, slug, cfg)
        covers = cfg.get("covers", "auto")
    if not r:
        return None
    complete = bool(cfg.get("complete")) and r.pop("complete", True)
    r.pop("complete", None)
    return {"region_id": slug, "date": day_covered(m["ts_published"], text, covers), "source": "ova_summary",
            **r, "complete": complete, "msg_ids": [m["id"]], "url": m.get("url"),
            "parser": "parse_kharkiv_daily" if cfg["parser"] == "kharkiv" else "parse_oblast_daily.generic"}


NUM_FIELDS = ("missiles", "drones", "fpv", "kab", "mlrs", "artillery", "attacks_n", "settlements_n", "shot_down", "killed", "injured")


def merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k in NUM_FIELDS:
        vals = [v for v in (a.get(k), b.get(k)) if v is not None]
        out[k] = max(vals) if vals else None
    for k in ("attacked", "complete"):
        out[k] = bool(a.get(k)) or bool(b.get(k))
    ca = [v for v in (a.get("city_attacked"), b.get("city_attacked")) if v is not None]
    out["city_attacked"] = any(ca) if ca else None
    out["msg_ids"] = sorted(set(a["msg_ids"]) | set(b["msg_ids"]))
    return out


def parse_messages(msgs, cfgs=None):
    """msgs: iterable of collector rows (id, source_id 'tg:<handle>', ts_published, text, url). -> {(region, date): row}"""
    cfgs = cfgs or channel_config()
    rows = {}
    for m in msgs:
        handle = m["source_id"].split(":", 1)[1].lower()
        if handle not in cfgs or not m.get("ts_published"):
            continue
        slug, cfg = cfgs[handle]
        r = parse_post(m, slug, cfg)
        if r:
            k = (r["region_id"], r["date"])
            rows[k] = merge(rows[k], r) if k in rows else r
    return rows


def main(since=None):
    cfgs = channel_config()
    files = []
    for h in cfgs:
        for d in ("ova", "telegram"):
            files += [p for p in glob.glob(str(ROOT / "data" / d / "*.jsonl")) if Path(p).name.lower().startswith(h)]

    def msgs():
        for p in sorted(set(files)):
            for line in open(p, encoding="utf-8"):
                m = json.loads(line)
                if since and (m.get("ts_published") or "") < since:
                    continue
                yield m
    rows = parse_messages(msgs(), cfgs)
    out = ROOT / "data" / "oblast_daily.jsonl"
    out.parent.mkdir(exist_ok=True)
    out.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for _, v in sorted(rows.items())), encoding="utf-8")
    by = {}
    for (s, _), v in rows.items():
        by.setdefault(s, []).append(v)
    print(f"{len(rows)} oblast-days from {len(files)} files -> {out}")
    for s, vs in sorted(by.items()):
        print(f"  {s:18s} days {len(vs):4d}  attacked {sum(v['attacked'] for v in vs):4d}  with missiles {sum(bool(v['missiles']) for v in vs):3d}  complete {sum(v['complete'] for v in vs):4d}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO date: ignore older posts")
    main(ap.parse_args().since)
