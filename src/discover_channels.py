"""Discover public Telegram channels per region by probing handle patterns on t.me/s/<handle>.

Only reports channels that (a) have a public preview, (b) posted recently, and (c) whose title looks regional.
Everything found is a CANDIDATE: a human must confirm that the channel is the real one (impostor channels with
similar names exist). Subscriber counts are shown to help with that.

Usage: python src/discover_channels.py [--max-age-h 72] [--extra handle1 handle2 ...]
Output: data/discovery.json and a table on stdout.
"""
import argparse
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# region -> (handle roots, title keywords in uk/ru/en)
REGIONS = {
    "Vinnytska": (["vinnytsia", "vinnytsya", "vinnitsa", "vinnica"], ["вінниц", "винниц", "vinnyt"]),
    "Volynska": (["volyn", "lutsk"], ["волин", "волын", "луцьк", "луцк", "volyn", "lutsk"]),
    "Dnipropetrovska": (["dnipro", "dp", "dnepr", "dnipropetrovsk"], ["дніпро", "днепр", "dnipro"]),
    "Donetska": (["donetsk", "donetska", "donbas"], ["донеч", "донецк", "donetsk"]),
    "Zhytomyrska": (["zhytomyr", "zhitomir"], ["житомир", "zhytomyr"]),
    "Zakarpatska": (["zakarpattia", "zakarpatia", "uzhhorod"], ["закарпат", "ужгород", "zakarpat"]),
    "Zaporizka": (["zaporizhzhia", "zaporizhia", "zp", "zaporozhye"], ["запоріж", "запорож", "zaporizh"]),
    "Ivano-Frankivska": (["ivanofrankivsk", "if", "frankivsk", "ivano_frankivsk"], ["франківськ", "франковск", "frankivsk"]),
    "Kyivska": (["kyiv", "kiev", "kyivska", "kyivregion"], ["київ", "киев", "kyiv"]),
    "Kirovohradska": (["kirovohrad", "kropyvnytskyi", "kropivnitskiy"], ["кіровоград", "кировоград", "кропивн", "kirovohrad"]),
    "Luhanska": (["luhansk", "lugansk"], ["луган", "luhansk"]),
    "Lvivska": (["lviv", "lvov"], ["львів", "львов", "lviv"]),
    "Mykolaivska": (["mykolaiv", "nikolaev", "mykolayiv"], ["миколаїв", "николаев", "mykolaiv"]),
    "Odeska": (["odesa", "odessa"], ["одес", "odes"]),
    "Poltavska": (["poltava"], ["полтав", "poltav"]),
    "Rivnenska": (["rivne", "rovno"], ["рівн", "ровн", "rivne"]),
    "Sumska": (["sumy", "sumska", "sumi"], ["сум", "sumy"]),
    "Ternopilska": (["ternopil", "ternopol"], ["терноп", "ternopil"]),
    "Kharkivska": (["kharkiv", "kharkov", "kh"], ["харк", "kharkiv"]),
    "Khersonska": (["kherson"], ["херсон", "kherson"]),
    "Khmelnytska": (["khmelnytskyi", "khmelnitsky", "khmelnytsky"], ["хмельниц", "khmelnyt"]),
    "Cherkaska": (["cherkasy", "cherkassy"], ["черкас", "cherkas"]),
    "Chernivetska": (["chernivtsi", "chernovtsy"], ["чернівц", "черновц", "chernivt"]),
    "Chernihivska": (["chernihiv", "chernigov"], ["черніг", "чернигов", "chernih"]),
    "Kyiv City": (["kyiv", "kiev"], ["київ", "киев", "kyiv"]),
}
PATTERNS = ["{r}oda", "{r}_oda", "{r}ova", "{r}_ova", "{r}oblast", "{r}_oblast", "monitor_{r}", "{r}_monitor",
            "{r}monitor", "{r}_monitoring", "{r}_alerts", "{r}_alarm", "{r}trivoga", "{r}_trivoga",
            "{r}_news", "{r}news", "{r}_online", "{r}ua", "{r}_ua", "{r}_air", "{r}_radar", "{r}radar"]
UA = {"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)"}


def probe(handle):
    try:
        req = urllib.request.Request(f"https://t.me/s/{handle}", headers=UA)
        page = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    if "tgme_widget_message_wrap" not in page:
        return None
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    title = re.sub(r"\s*[–-]\s*Telegram\s*$", "", title.group(1).strip()) if title else ""
    times = re.findall(r'<time datetime="([^"]+)"', page)
    age = None
    if times:
        dt = datetime.fromisoformat(times[-1].replace("Z", "+00:00"))
        age = round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
    subs = re.search(r'counter_value">([^<]+)</span>\s*<span class="counter_type">subscribers', page)
    return {"handle": handle, "title": title[:70], "age_h": age, "subscribers": subs.group(1) if subs else None}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-h", type=float, default=72)
    ap.add_argument("--extra", nargs="*", default=[])
    a = ap.parse_args()
    jobs = {}
    for region, (roots, _) in REGIONS.items():
        for r in roots:
            for p in PATTERNS:
                jobs.setdefault(p.format(r=r), region)
    for h in a.extra:
        jobs.setdefault(h, "?")
    handles = list(jobs)
    print(f"probing {len(handles)} handles...")
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(probe, handles))
    found = []
    for h, res in zip(handles, results):
        if not res:
            continue
        region = jobs[h]
        kws = REGIONS.get(region, ([], []))[1]
        regional = any(k in res["title"].lower() for k in kws) if kws else True
        res.update(region=region, title_matches_region=regional)
        if res["age_h"] is not None and res["age_h"] <= a.max_age_h:
            found.append(res)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "discovery.json").write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in sorted(found, key=lambda x: (x["region"], not x["title_matches_region"], x["handle"])):
        flag = "REGION" if r["title_matches_region"] else "?     "
        print(f"{r['region']:17} {flag} @{r['handle']:28} subs={str(r['subscribers']):8} age_h={r['age_h']:<6} {r['title']}")
    print(f"{len(found)} active candidates")
