"""Lawyers named in court decision texts -> person_case_links (migration 031): who represented whom in which case.

ЄДРСР texts hide the parties («ОСОБА_1») but often keep the lawyers: «адвоката Бондика Валерія Анатолійовича»,
«адвокат Бондик В. А.», «адвокатом Галадієм А.М.». Each mention is resolved against the register of lawyers (ЄРАУ,
lake lawyers.parquet) with Ukrainian case endings stripped:
  one candidate      -> a link marked certainty 'можливо' (a unique name in the register is not proof of identity)
  several candidates -> no link; the mention is kept as 'неоднозначно' with the number of candidates
A full name (surname + name + patronymic) and surname + initials are both accepted; the match level is stored.

  python src/person_cases.py build [--dry-run]
  python src/person_cases.py test "адвоката Бондика Валерія Анатолійовича"
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

U = "А-ЯІЇЄҐ"
L = "а-яіїєґ'’ʼ"
MENTION = re.compile(rf"адвокат[{L}]*\s+([{U}][{L}-]+)\s+(?:([{U}])\.\s?([{U}])\.|([{U}][{L}]+)\s+([{U}][{L}]+))")
# nominative endings a case form replaces; the longest that leaves a 3+ letter stem is cut
ENDINGS = ("ський", "цький", "зький", "ий", "ій", "ой", "а", "я", "о", "й", "ь", "е", "є")


def stem(w: str) -> str:
    """Lower-case stem without the nominative ending.
    >>> stem("Бондик"), stem("Галадій"), stem("Яворський"), stem("Людмила"), stem("Бойко")
    ('бондик', 'галад', 'яворськ', 'людмил', 'бойк')
    """
    w = w.lower().replace("’", "'").replace("ʼ", "'")
    for e in sorted(ENDINGS, key=len, reverse=True):
        if w.endswith(e) and len(w) - len(e) >= 3:
            return w[: -len(e)] + ("ськ" if e == "ський" else "цьк" if e == "цький" else "зьк" if e == "зький" else "")
    return w


def form_of(token: str, base: str) -> bool:
    """A case form of the nominative `base`: same word, or the stem plus up to 3 letters of ending.
    >>> form_of("Бондика", "Бондик"), form_of("Галадієм", "Галадій"), form_of("Яворського", "Яворський"), form_of("Бойко", "Бойко"), form_of("Бондаренка", "Бондар")
    (True, True, True, True, False)
    """
    t, b = token.lower().replace("’", "'").replace("ʼ", "'"), base.lower().replace("’", "'").replace("ʼ", "'")
    if t == b:
        return True
    s = stem(base)
    return t.startswith(s) and len(t) - len(s) <= 3


class Register:
    def __init__(self, rows):
        """rows: (erau_id, full name 'Прізвище Ім'я По батькові', status)"""
        self.by3 = defaultdict(list)
        for erau_id, name, status in rows:
            parts = name.split()
            if len(parts) < 2:
                continue
            self.by3[stem(parts[0])[:3]].append((erau_id, parts, name, status))

    def resolve(self, surname, i1=None, i2=None, name=None, patr=None):
        """-> (candidates [(erau_id, name, status)], level 'full' | 'initials')."""
        out = []
        for erau_id, parts, full, status in self.by3.get(stem(surname)[:3], []):
            if not form_of(surname, parts[0]):
                continue
            n, p = parts[1], parts[2] if len(parts) > 2 else ""
            if name:
                if form_of(name, n) and (not patr or (p and form_of(patr, p))):
                    out.append((erau_id, full, status))
            elif n[:1].upper() == i1 and (not i2 or p[:1].upper() == i2):
                out.append((erau_id, full, status))
        return out, ("full" if name else "initials")


def mentions(text):
    for m in MENTION.finditer(text or ""):
        sur, i1, i2, nm, pt = m.groups()
        if sur.startswith("ОСОБА"):
            continue
        yield m.group(0), sur, i1, i2, nm, pt


def resolve_doc(reg, text):
    """One result per person mentioned in a decision. Mentions with the same surname and initials are one person: the
    full form decides when there is one, so «Бойко Людмила Вікторівна» (not in the register) is not turned into
    «Бойко Людмила Володимирівна» by its short form «Бойко Л. В.».
    >>> r = Register([("1", "Бойко Людмила Володимирівна", "діє"), ("2", "Бондик Валерій Анатолійович", "діє")])
    >>> [(lv, len(c)) for _, lv, c in resolve_doc(r, "адвокат Бойко Людмила Вікторівна ... адвоката Бойко Л. В. ... адвокат Бондик В. А.")]
    [('full', 0), ('initials', 1)]
    """
    groups = {}
    for raw, sur, i1, i2, nm, pt in mentions(text):
        sig = (stem(sur), (i1 or (nm or "")[:1]).upper(), (i2 or (pt or "")[:1]).upper())
        g = groups.setdefault(sig, {"full": None, "short": None})
        if nm and g["full"] is None:
            g["full"] = (raw, sur, i1, i2, nm, pt)
        elif not nm and g["short"] is None:
            g["short"] = (raw, sur, i1, i2, nm, pt)
    out = {}
    for g in groups.values():
        raw, sur, i1, i2, nm, pt = g["full"] or g["short"]
        cands, level = reg.resolve(sur, i1, i2, nm, pt)
        # different case forms of one surname («Галадія», «Галадієм») resolve to the same lawyer: one row, the fullest
        key = tuple(sorted(c[0] for c in cands)) or (raw,)
        if key not in out or (level == "full" and out[key][1] == "initials"):
            out[key] = (raw, level, cands)
    yield from out.values()


def load_register():
    import pyarrow.parquet as pq
    from lake import LAKE, fetch_release
    p = LAKE / "lawyers.parquet"
    LAKE.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        fetch_release("lawyers.parquet")
    t = pq.read_table(p, columns=["erau_id", "name", "status"]).to_pylist()
    seen = {}
    for r in t:
        seen.setdefault(r["erau_id"], (r["erau_id"], r["name"], r["status"]))
    return Register(seen.values())


def build(a):
    from load_to_db import connect
    reg = load_register()
    rows = []
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT doc_id, cause_num, text FROM court_decisions WHERE text IS NOT NULL AND text ~* 'адвокат'")
        for doc_id, cause, text in cur.fetchall():
            for raw, level, cands in resolve_doc(reg, text):
                one = cands[0] if len(cands) == 1 else None
                rows.append((cause, doc_id, "адвокат", raw[:200], level, len(cands),
                             "можливо" if one else ("неоднозначно" if cands else "немає в реєстрі"),
                             ("person:" + re.sub(r"\s+", " ", one[1].strip().lower())) if one else None, one[0] if one else None, one[1] if one else None))
        stat = defaultdict(int)
        for r in rows:
            stat[r[6]] += 1
        print(f"decisions with texts mentioning a lawyer: {len({r[1] for r in rows})}; mentions: {len(rows)}; {dict(stat)}")
        for r in rows[:8]:
            print("  ", r[0], "|", r[3], "->", r[6], r[9] or "", f"({r[5]} candidates, {r[4]})")
        if a.dry_run:
            return
        cur.execute("CREATE TEMP TABLE pc (LIKE person_case_links INCLUDING DEFAULTS) ON COMMIT DROP")
        with cur.copy("COPY pc (cause_num, doc_id, role, mention, match_level, candidates, certainty, person_id, ref_id, person_name) FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)
        cur.execute("DELETE FROM person_case_links WHERE role = 'адвокат'")
        cur.execute("INSERT INTO person_case_links SELECT * FROM pc")
        conn.commit()
        print("person_case_links rows", len(rows))


def test(a):
    reg = load_register()
    for raw, sur, i1, i2, nm, pt in mentions(a.text):
        cands, level = reg.resolve(sur, i1, i2, nm, pt)
        print(raw, "->", level, len(cands), cands[:5])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--dry-run", action="store_true")
    b.set_defaults(fn=build)
    t = sub.add_parser("test")
    t.add_argument("text")
    t.set_defaults(fn=test)
    a = ap.parse_args()
    a.fn(a)
