"""Check which public Telegram channels expose a web preview (t.me/s/<name>) and are active.

No API keys needed. Usage: python src/check_channels.py [name ...]
"""
import re
import sys
import urllib.request
from datetime import datetime, timezone

CANDIDATES = {
    # Ukraine: official / operational
    "kpszsu": ("UA", "official", "Air Force of Ukraine"),
    "GeneralStaffZSU": ("UA", "official", "General Staff"),
    "V_Zelenskiy_official": ("UA", "official", "President's channel"),
    "synegubov": ("UA", "official", "Kharkiv oblast governor"),
    "kharkivoda": ("UA", "official", "Kharkiv OVA"),
    "ukrainian_alarm_map": ("UA", "osint", "alerts (guess)"),
    "war_monitor_ua": ("UA", "osint", "monitoring (guess)"),
    "monitorwarr": ("UA", "osint", "monitoring (guess)"),
    "kharkiv_monitor": ("UA", "osint", "Kharkiv monitoring (guess)"),
    "napramok": ("UA", "osint", "Napramok monitoring"),
    "radar_aerobaza": ("UA", "osint", "air monitoring"),
    # Russia: official / state media
    "mod_russia": ("RU", "official", "MoD Russia"),
    "MID_Russia": ("RU", "official", "MFA Russia"),
    "news_kremlin": ("RU", "official", "Kremlin"),
    "tass_agency": ("RU", "state_media", "TASS"),
    "rian_ru": ("RU", "state_media", "RIA Novosti"),
    "rt_russian": ("RU", "state_media", "RT"),
    "SolovievLive": ("RU", "state_media", "Soloviev"),
    "medvedev_telegram": ("RU", "official", "Medvedev"),
    # Russia: milbloggers
    "voenkorKotenok": ("RU", "milblogger", "Kotenok"),
    "RVvoenkor": ("RU", "milblogger", "Russian Spring"),
    "wargonzo": ("RU", "milblogger", "WarGonzo"),
    "rybar": ("RU", "milblogger", "Rybar"),
    "dva_majors": ("RU", "milblogger", "Two Majors"),
    "boris_rozhin": ("RU", "milblogger", "Colonelcassad"),
    "sashakots": ("RU", "milblogger", "Kots"),
    # Independent / analysts
    "meduzalive": ("RU", "independent", "Meduza"),
    "ukrpravda_news": ("UA", "independent", "Ukrainska Pravda"),
    "nabu_ua": ("UA", "official", "NABU (guess)"),
}


def check(name):
    url = f"https://t.me/s/{name}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return {"name": name, "ok": False, "err": str(e)[:60]}
    posts = re.findall(r'class="tgme_widget_message_wrap', html)
    if not posts:
        return {"name": name, "ok": False, "err": "no public preview / not found"}
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    times = re.findall(r'<time datetime="([^"]+)"', html)
    last = times[-1] if times else None
    age_h = None
    if last:
        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age_h = round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
    return {
        "name": name, "ok": True, "title": (title.group(1).strip() if title else "")[:60],
        "posts_on_page": len(posts), "last_post": last, "last_post_age_h": age_h,
    }


if __name__ == "__main__":
    names = sys.argv[1:] or list(CANDIDATES)
    for n in names:
        r = check(n)
        meta = CANDIDATES.get(n, ("?", "?", ""))
        if r["ok"]:
            print(f"OK   {n:22} {meta[0]} {meta[1]:12} posts={r['posts_on_page']:2} age_h={r['last_post_age_h']}  {r['title']}")
        else:
            print(f"FAIL {n:22} {meta[0]} {meta[1]:12} {r['err']}")
