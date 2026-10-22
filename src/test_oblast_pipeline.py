"""Synthetic end-to-end check of the oblast pipeline, no database and no network (pytest not needed):

  python src/test_oblast_pipeline.py

1. parse_oblast_daily: Kharkiv weapon block, Zaporizhzhia daily summary (FPV kept apart), a national repost rejected.
2. gazetteer: KATOTTG resolve() and the generated per-oblast regex (same alternatives as migration 016).
3. long format: build_long -> label_oblast -> design_long -> expanding-window backtest (the live code path of
   nowcast.py) for a pooled baseline and candidate, block-bootstrap CI over dates, shuffled-date control.
   a) a planted region signal must be found and must beat its shuffled controls;
   b) with no signal the candidate must not beat the baseline beyond its CI / controls;
   c) no leakage: changing outcomes on D-1 and later never changes the features of day D.
Exit code 1 on any failure. The numbers here say nothing about real data.
"""
import doctest
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_gazetteer  # noqa: E402
import features  # noqa: E402
import nowcast  # noqa: E402
import parse_oblast_daily as P  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def msg(handle, ts, text, n=1):
    return {"id": f"tg:{handle}:{n}", "source_id": f"tg:{handle}", "ts_published": ts, "text": text, "url": f"https://t.me/{handle}/{n}"}


def test_parser():
    kh = ("📍 Протягом минулої доби ворожих ударів зазнали м. Харків і 12 населених пунктів Харківської області.\n"
          "▪️Внаслідок обстрілів 6 людей загинули, постраждала 21 людина.\n"
          "Ворог активно застосовував по Харківщині різні види озброєння:\n"
          "▪️4 ракети;\n▪️5 БпЛА типу «Герань-2»;\n▪️1 fpv-дрон;\n▪️89 БпЛА (тип встановлюється).\n"
          "Пошкоджено та зруйновано обʼєкти цивільної інфраструктури.")
    zp = ("⏺20 людей загинули, ще 37 поранені внаслідок ворожих атак на Запоріжжя та Запорізький район\n\n"
          "Загалом упродовж доби окупанти завдали 904 удари по 51 населеному пункту Запорізької області.\n"
          "▪️Війська рф здійснили 27 авіаударів по Запоріжжю, Оріхову.\n"
          "▪️616 БпЛА різної модифікації (переважно FPV) атакували Запоріжжя, Оріхів.\n"
          "▪️Зафіксовано 35 обстрілів з РСЗВ по Оріхову.\n▪️Внаслідок ракетного удару: 2 ракети по Запоріжжю.")
    national = ("Знову російська балістична атака кількома хвилями за ніч. Загалом росіяни застосували 16 балістичних ракет, "
                "і це була цілеспрямована атака проти Києва та Київщини. Також більш ніж 200 дронів, від Харківщини та "
                "Дніпровщини до Волині, Рівненщини та Одещини. За минулу добу пошкоджено об'єкти у Вінницькій, Волинській, "
                "Кіровоградській, Миколаївській та Одеській областях.")
    cherk = ("Доброго ранку, Черкащино! За час повітряних тривог 21–22 жовтня в області знешкоджено 13 російських БпЛА. "
             "Наслідки ворожих атак маємо в Умані.")
    rows = P.parse_messages([msg("synegubov", "2026-10-20T05:41:00+00:00", kh, 1),
                             msg("ivan_fedorov_zp", "2026-10-11T04:00:00+00:00", zp, 2),
                             msg("odeskaODA", "2026-10-19T05:03:00+00:00", national, 3),
                             msg("cherkaskaODA", "2026-10-22T02:46:00+00:00", cherk, 4)])
    dn = ("Дніпропетровщина \n20 жовтня \n18:30\n\nДвоє людей загинули. Понад 30 разів ворог атакував чотири райони. "
          "Бив безпілотниками, артилерією та авіабомбами.")
    weekly = ("Коротко про головне за підсумками минулого тижня. Протягом тижня ворог атакував Хмельниччину. "
              "Силами ППО збито 70 безпілотників. За минулу добу збито 12 БпЛА.")
    extra = P.parse_messages([msg("dnipropetrovskaODA", "2026-10-20T15:30:00+00:00", dn, 5),
                              msg("khmelnytskaODA", "2026-10-18T06:00:00+00:00", weekly, 6)])
    d = extra.get(("dnipropetrovska", "2026-10-20"))
    check(d is not None and d["drones"] == 0 and d["attacks_n"] == 30 and d["killed"] is None,
          f"'18:30' is not a casualty count and '30 разів ... безпілотниками' is not 30 drones: {d and {f: d[f] for f in ('drones', 'attacks_n', 'killed')}}")
    check(not any(r == "khmelnytska" for r, _ in extra), "weekly roundup is skipped")
    k = rows.get(("kharkivska", "2026-10-19"))
    check(k is not None and (k["missiles"], k["drones"], k["fpv"], k["complete"], k["killed"]) == (4, 94, 1, True, 6),
          f"Kharkiv block -> missiles 4, drones 5+89, fpv 1, complete, killed 6: {k and {f: k[f] for f in ('missiles', 'drones', 'fpv', 'complete', 'killed')}}")
    z = rows.get(("zaporizka", "2026-10-10"))
    check(z is not None and (z["missiles"], z["drones"], z["fpv"], z["kab"], z["mlrs"], z["attacks_n"], z["settlements_n"], z["killed"]) == (2, 0, 616, 27, 35, 904, 51, 20),
          f"Zaporizhzhia: FPV-dominated drones kept out of strike drones, air strikes as kab: {z and {f: z[f] for f in ('missiles', 'drones', 'fpv', 'kab', 'mlrs', 'attacks_n', 'settlements_n', 'killed')}}")
    check(not any(r == "odeska" for r, _ in rows), "national repost in an oblast channel is rejected by the scope guard")
    c = rows.get(("cherkaska", "2026-10-22"))
    check(c is not None and c["drones"] == 13 and c["shot_down"] == 13 and not c["complete"],
          f"Cherkasy \"тривоги 21–22 жовтня\" (night into the 22nd) dated the posting day, shot-down count kept: {c and {f: c[f] for f in ('drones', 'shot_down', 'complete')}}")


def test_gazetteer():
    places, extra = build_gazetteer.load()
    for name, want in [("Харків", "kharkivska"), ("Златопіль", "kharkivska"), ("Берестин", "kharkivska"), ("Кривий Ріг", "dnipropetrovska"),
                       ("Миколаїв", "mykolaivska"), ("Ужгород", "zakarpatska"), ("Київ", "kyiv-city"), ("Сімферополь", "krym")]:
        check(build_gazetteer.resolve(name, places, extra) == want, f"resolve({name}) = {want}")
    check(build_gazetteer.resolve("Олексіївка", places, extra) is None, "a village name present in many oblasts stays unresolved")
    pats = build_gazetteer.region_patterns()
    rx = {r: re.compile(build_gazetteer.sql_regex(v)) for r, v in pats.items()}  # the exact pattern string written to 016

    def regions(t):
        t = t.lower().translate(build_gazetteer.APOS)
        return sorted(r for r, x in rx.items() if x.search(t))
    cases = [("Удари по Харкову і Полтаві тривають", ["kharkivska", "poltavska"]),
             ("Удар по Кременчуку", ["poltavska"]),
             ("Взрывы в Днепре и Запорожье", ["dnipropetrovska", "zaporizka"]),
             ("Обстріл Тернополя вночі", ["ternopilska"]),
             ("Українська делегація у Брюсселі", [])]
    for t, want in cases:
        got = regions(t)
        check(got == want, f"regex: {t!r} -> {want} (got {got})")
    sql = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "016_rule_geo.sql").read_text(encoding="utf-8")
    check(all(f"THEN '{r}' END" in sql for r in pats) and sql.count("THEN '") == len(pats), "016_rule_geo.sql has one CASE per region from the same patterns")


def synth(seed, effect):
    """6 regions x 240 days. Region base rates differ; a regional threat share leads the target by one day when effect > 0."""
    rng = np.random.default_rng(seed)
    regions = ["kharkivska", "khersonska", "lvivska", "odeska", "sumska", "zakarpatska"]
    base = dict(zip(regions, [0.45, 0.35, 0.08, 0.2, 0.3, 0.03]))
    days = pd.date_range("2026-03-31", periods=240, freq="D")
    nat = pd.DataFrame({"d": days, "posts_ru": rng.integers(200, 350, len(days))})
    nat["l2_ru"] = np.clip(0.05 + 0.02 * rng.standard_normal(len(days)), 0, 1)
    geo, gt = [], []
    for r in regions:
        z = rng.standard_normal(len(days))
        z = np.convolve(z, [0.6, 0.4])[: len(days)]  # some autocorrelation
        n_thr = np.maximum(0, np.round(6 + 4 * z)).astype(int)
        geo.append(pd.DataFrame({"d": days, "region_id": r, "n_thr_geo_ru": n_thr}))
        share = n_thr / nat.posts_ru.to_numpy()
        logit = np.log(base[r] / (1 - base[r])) + effect * (share - share.mean()) / share.std()
        p = 1 / (1 + np.exp(-logit))
        hit = rng.random(len(days)) < p  # the signal row d is the window ending on D-1, so it leads D's outcome
        complete = rng.random(len(days)) < 0.8 if r != "zakarpatska" else np.zeros(len(days), bool)
        gt.append(pd.DataFrame({"region_id": r, "d": days, "missiles": np.where(hit, 8, 0), "drones": np.where(hit, 30, 2),
                                "complete": complete, "attacked": hit}))
    cov = pd.DataFrame({"d": days, "n_extracted": rng.integers(50, 300, len(days))})
    return features.build_long(nat, pd.concat(geo), pd.concat(gt), cov, days[0], days[-1], regions, 100)


def run_pair(df, controls):
    root = Path(__file__).resolve().parent.parent / "models"
    cand = json.loads((root / "ua_oblast_massive_v1.json").read_text())
    base = json.loads((root / "ua_oblast_massive_base_v1.json").read_text())
    rb, rc = nowcast.backtest_spec(df, base), nowcast.backtest_spec(df, cand)
    delta, ci, j = nowcast.compare(rc, rb, True)
    null = []
    for k in np.random.default_rng(3).integers(7, df.d.nunique() - 7, controls):
        dk, _, _ = nowcast.compare(nowcast.backtest_spec(df, cand, int(k)), rb, True)
        null.append(dk)
    pv = (1 + sum(x >= delta for x in null)) / (1 + len(null))
    mc = nowcast.metrics(rc.y, rc.p, rc.region_id)
    return delta, ci, pv, mc, len(j)


def test_long():
    df = synth(1, effect=1.2)
    check(set(df.columns) >= {"region_id", "d", "ob_massive", "ob_missile_day", "thr_geo_ru", "l2_ru"}, "build_long: long frame with targets and signals")
    known = df.groupby("region_id").ob_massive.apply(lambda s: s.notna().mean())
    check(known["zakarpatska"] < known["kharkivska"], f"unknown days stay NaN where no complete summary / extractor coverage (share known {known.round(2).to_dict()})")
    spec = json.loads((Path(__file__).resolve().parent.parent / "models" / "ua_oblast_massive_v1.json").read_text())
    F = features.design_long(df, spec)
    check({"reg_kharkivska", "base", "y2", "y2_m7", "l2_ru_1", "thr_geo_ru_1", "thr_geo_ru_m3"} <= set(F.columns), f"design_long columns: {sorted(F.columns)[:8]}...")
    # leakage: perturb every outcome from day D-1 on; features of day D must not move
    D = df.d.unique()[150]
    df2 = df.copy()
    late = df2.d >= D - pd.Timedelta(days=1)
    df2.loc[late, "ob_massive"] = 1 - df2.loc[late, "ob_massive"]
    F2 = features.design_long(df2, spec)
    rows = df.index[df.d == D]
    check(np.allclose(F.loc[rows].to_numpy(float), F2.loc[rows].to_numpy(float), equal_nan=True), "no leakage: outcomes on D-1 and later do not change features of D")

    delta, ci, pv, mc, n = run_pair(df, controls=8)
    print(f"     planted signal: Δ AUC {delta:+.3f} CI {ci}, shuffled-control p {pv:.3f}, candidate AUC {mc['auc']:.3f} within-region {mc['auc_within']:.3f}, n {n}")
    check(delta > 0.03 and ci is not None and ci[0] > 0 and pv <= 1 / 9 + 1e-9, "planted region signal: candidate beats baseline, CI above 0, beats every shuffled control")

    delta0, ci0, pv0, _, _ = run_pair(synth(2, effect=0.0), controls=8)
    print(f"     no signal:      Δ AUC {delta0:+.3f} CI {ci0}, shuffled-control p {pv0:.3f}")
    check(ci0 is not None and ci0[0] <= 0, "no signal: CI of Δ AUC includes 0 (no claimed gain)")

    # the frozen single-series Kharkiv pair still runs through the shared code path (region_id 'kharkivska')
    root = Path(__file__).resolve().parent.parent / "models"
    kh = df[df.region_id == "kharkivska"].reset_index(drop=True).rename(columns={"ob_missile_day": "kh_missile_day"})
    kc, kb = (json.loads((root / f).read_text()) for f in ("kh_missile_v1.json", "kh_missile_base_v1.json"))
    rkc, rkb = nowcast.backtest_spec(kh, kc), nowcast.backtest_spec(kh, kb)
    dk, cik, _ = nowcast.compare(rkc, rkb, False)
    check(len(rkc) > 100 and set(rkc.region_id) == {"kharkivska"} and dk is not None, f"kh_missile_v1 vs base still backtests: n {len(rkc)}, Δ AUC {dk:+.3f} CI {cik}")

    # live path: one forecast per region for the next day, probabilities in (0, 1)
    target = df.d.max()
    Fb = features.design_long(df, spec)
    rows = list(df.index[df.d == target])
    p, n_train = nowcast.fit_predict_rows(Fb, df[spec["target"]], (df.d < target - pd.Timedelta(days=1)) & df[spec["target"]].notna(), rows, spec["C"])
    check(p is not None and len(p) == 6 and ((p > 0) & (p < 1)).all(), f"predict: {len(rows)} regional forecasts for {target.date()} (n_train {n_train})")


if __name__ == "__main__":
    for mod in (features, build_gazetteer):
        r = doctest.testmod(mod)
        check(r.failed == 0, f"doctests in {mod.__name__} ({r.attempted} run)")
    test_parser()
    test_gazetteer()
    test_long()
    print("\nALL OK" if not FAIL else f"\n{len(FAIL)} FAILED")
    sys.exit(1 if FAIL else 0)
