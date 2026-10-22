"""Oblast registry: one place for region ids, names and the oblast-administration Telegram channels.

Region ids are the alert-map slugs already used by `alerts.region_id` (load_to_db.slug("Kharkivska oblast") ->
"kharkivska"), `extracted_events.region` and extract_statements.REGIONS. Crimea and Sevastopol are kept out of the
panel (no alerts, no administration summaries), but the gazetteer still knows them so their places are not
mis-attributed to another oblast.

Channel config (`channels`): the official oblast administration (OVA) channel(s) whose daily summaries become
`oblast_daily` rows (src/parse_oblast_daily.py). Fields:
  handle    t.me handle (checked 2026-10-22 via t.me/s/<handle>: public, active, title names the OVA/head)
  parser    "kharkiv" = the @synegubov weapon block (parse_kharkiv_daily.parse), otherwise "generic"
  complete  true only when the channel's daily summary lists every weapon class with a number, so an absent
            class means zero. Only complete rows give NEGATIVE target days; incomplete ones give positives only.
  covers    "prev" when the morning summary covers the previous day (Kyiv), "auto" = previous day if posted
            before 12:00 Kyiv and the text says "минулої доби / протягом доби", else the posting day.
A region with no verified handle has channels = [] (Sumy and Rivne: no official channel found by probing; add one
after a manual check, impostor channels exist - e.g. t.me/sumy_oda is a Russian-side channel).

  python src/oblasts.py --handles      # space-separated handles for collect_telegram.py
"""
import argparse

# slug -> uk adjective (as in the KATOTTG codifier), genitive-ish stem of the "-щина" name, oblast centre
OBLASTS = {
    "vinnytska": {"name": "Вінницька", "shchyna": "вінниччин", "center": "Вінниця",
                  "channels": [{"handle": "vinnytsiaoda", "parser": "generic", "complete": False, "covers": "auto"}]},
    "volynska": {"name": "Волинська", "shchyna": "волин", "center": "Луцьк",
                 "channels": [{"handle": "volynskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "dnipropetrovska": {"name": "Дніпропетровська", "shchyna": "дніпропетровщин", "center": "Дніпро",
                        # morning and evening roundups: "Дніпропетровщина\n17 жовтня\n18:30\nПонад 30 разів ворог атакував ..."
                        "channels": [{"handle": "dnipropetrovskaODA", "parser": "generic", "complete": False, "covers": "auto",
                                      "start": r"дніпропетровщина\s+\d{1,2}\s+\w+\s+\d{1,2}:\d{2}"}]},
    "donetska": {"name": "Донецька", "shchyna": "донеччин", "center": "Краматорськ",
                 # "Оперативна ситуація по області станом на ранок 14 жовтня": the past 24 h, casualties and damage, no weapon counts
                 "channels": [{"handle": "vadymfilashkin", "parser": "generic", "complete": False, "covers": "prev",
                               "start": r"оперативна ситуація по області"}]},
    "zhytomyrska": {"name": "Житомирська", "shchyna": "житомирщин", "center": "Житомир",
                    "channels": [{"handle": "zhytomyrskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "zakarpatska": {"name": "Закарпатська", "shchyna": "закарпатт", "center": "Ужгород",
                    "channels": [{"handle": "zakarpatskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "zaporizka": {"name": "Запорізька", "shchyna": "запоріжж", "center": "Запоріжжя",
                  # daily "Загалом упродовж доби окупанти завдали N ударів ..." with авіаудари / БпЛА / РСЗВ / артилерія.
                  # Not complete: "N БпЛА різної модифікації (переважно FPV)" lumps Shaheds with FPV, so the strike-drone
                  # count (the massed-strike definition) is unknown.
                  "channels": [{"handle": "ivan_fedorov_zp", "parser": "generic", "complete": False, "covers": "prev",
                                "start": r"упродовж доби окупанти завдали"},
                               {"handle": "zoda_gov_ua", "parser": "generic", "complete": False, "covers": "auto"}]},
    "ivano-frankivska": {"name": "Івано-Франківська", "shchyna": "прикарпатт", "center": "Івано-Франківськ",
                         "channels": [{"handle": "if_oda", "parser": "generic", "complete": False, "covers": "auto"}]},
    "kyivska": {"name": "Київська", "shchyna": "київщин", "center": None,
                "channels": [{"handle": "kyivoda", "parser": "generic", "complete": False, "covers": "auto"}]},
    "kirovohradska": {"name": "Кіровоградська", "shchyna": "кіровоградщин", "center": "Кропивницький",
                      "channels": [{"handle": "kirovohradskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "luhanska": {"name": "Луганська", "shchyna": "луганщин", "center": None,
                 "channels": [{"handle": "luhanskaVTSA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "lvivska": {"name": "Львівська", "shchyna": "львівщин", "center": "Львів",
                "channels": [{"handle": "kozytskyy_maksym_official", "parser": "generic", "complete": False, "covers": "auto"}]},
    "mykolaivska": {"name": "Миколаївська", "shchyna": "миколаївщин", "center": "Миколаїв",
                    "channels": [{"handle": "mykolaiv_ova", "parser": "generic", "complete": False, "covers": "auto"}]},
    "odeska": {"name": "Одеська", "shchyna": "одещин", "center": "Одеса",
               "channels": [{"handle": "odeskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "poltavska": {"name": "Полтавська", "shchyna": "полтавщин", "center": "Полтава",
                  "channels": [{"handle": "poltavskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "rivnenska": {"name": "Рівненська", "shchyna": "рівненщин", "center": "Рівне", "channels": []},
    "sumska": {"name": "Сумська", "shchyna": "сумщин", "center": "Суми", "channels": []},
    "ternopilska": {"name": "Тернопільська", "shchyna": "тернопільщин", "center": "Тернопіль",
                    "channels": [{"handle": "ternopilskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "kharkivska": {"name": "Харківська", "shchyna": "харківщин", "center": "Харків",
                   "channels": [{"handle": "synegubov", "parser": "kharkiv", "complete": True, "covers": "prev"}]},
    "khersonska": {"name": "Херсонська", "shchyna": "херсонщин", "center": "Херсон",
                   # "Оперативна інформація з 06:00 <D-1> по 06:00 <D>. Минулої доби ..."
                   "channels": [{"handle": "khersonskaODA", "parser": "generic", "complete": False, "covers": "prev",
                                 "start": r"оперативна інформація з 06:00"}]},
    "khmelnytska": {"name": "Хмельницька", "shchyna": "хмельниччин", "center": "Хмельницький",
                    "channels": [{"handle": "khmelnytskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "cherkaska": {"name": "Черкаська", "shchyna": "черкащин", "center": "Черкаси",
                  "channels": [{"handle": "cherkaskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "chernivetska": {"name": "Чернівецька", "shchyna": "буковин", "center": "Чернівці",
                     "channels": [{"handle": "chernivetskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "chernihivska": {"name": "Чернігівська", "shchyna": "чернігівщин", "center": "Чернігів",
                     "channels": [{"handle": "chernigivskaODA", "parser": "generic", "complete": False, "covers": "auto"}]},
    "kyiv-city": {"name": "Київ", "shchyna": None, "center": "Київ",
                  "channels": [{"handle": "tkachenkotymur", "parser": "generic", "complete": False, "covers": "auto"}]},
}
# KATOTTG first-level names outside the panel (gazetteer only)
EXTRA_AREAS = {"Автономна Республіка Крим": "krym", "Севастополь": "sevastopol"}
PANEL_REGIONS = sorted(OBLASTS)


def slug_for_codifier(name: str):
    """KATOTTG level-1 name ('Харківська', 'Київ', 'Автономна Республіка Крим') -> region slug."""
    name = name.strip()
    if name == "Київ":
        return "kyiv-city"
    if name in EXTRA_AREAS:
        return EXTRA_AREAS[name]
    for slug, o in OBLASTS.items():
        if o["name"] == name and slug != "kyiv-city":
            return slug
    return None


def channel_config():
    """handle (lowercase) -> (region slug, channel config)."""
    return {c["handle"].lower(): (slug, c) for slug, o in OBLASTS.items() for c in o["channels"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--handles", action="store_true")
    a = ap.parse_args()
    if a.handles:
        print(" ".join(c["handle"] for o in OBLASTS.values() for c in o["channels"] if c["handle"] != "synegubov"))
    else:
        for s, o in OBLASTS.items():
            print(f"{s:18s} {o['name']:18s} {[c['handle'] for c in o['channels']]}")
