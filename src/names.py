"""Name matching that tolerates Ukrainian/Russian inflection but not other surnames.
«Каменська Еліна» must match «Каменської Еліни», «Еліна Каменська», «Каменська Е.» — and not «Каменських» or «Каменський».
Produces one regex usable both in Python (\\b) and in Postgres ARE (\\m / \\M)."""
import re

END = r"(?:ієві|ієм|ія|ію|ії|ою|ею|ої|ій|ий|ого|ому|им|ім|ем|ом|а|я|и|і|ї|у|ю|е|є|о|ь)?"
CYR = re.compile(r"^[А-ЯІЇЄҐа-яіїєґ'’-]+$")


def stem(tok):
    if not CYR.match(tok) or len(tok) < 4:
        return tok
    return re.sub(r"(ий|ій|ою|ею|ої|ого|ому|[аяйьоеиіую])$", "", tok) or tok


def name_regex(q, pg=False):
    """q: 'Прізвище Імʼя [По батькові]' (or just a surname). Returns a case-insensitive pattern."""
    b0, b1 = ("\\m", "\\M") if pg else ("\\b", "\\b")
    toks = q.split()
    if not toks or not CYR.match(toks[0]):
        return re.escape(q)
    tok = lambda t: b0 + re.escape(stem(t)) + END + b1  # noqa: E731
    sur = tok(toks[0])
    if len(toks) == 1:
        return sur
    first = "(?:" + tok(toks[1]) + "|" + b0 + re.escape(toks[1][0]) + r"\.?" + ")"
    sep = r"[\s,]{1,3}"
    patr = (sep + "(?:" + tok(toks[2]) + "|" + re.escape(toks[2][0]) + r"\.?)?") if len(toks) > 2 else ""
    return "(?:" + sur + sep + first + patr + "|" + first + sep + sur + ")"


def name_matches(q, text):
    return re.search(name_regex(q), text or "", re.I) is not None


if __name__ == "__main__":
    tests = [("Каменська Еліна", "доцент Каменська Еліна Петрівна", True), ("Каменська Еліна", "Настя Каменських видала", False),
             ("Каменська Еліна", "Еліни Каменської", True), ("Каменська Еліна", "Каменська Е. П.", True), ("Каменська Еліна", "Каменський Олег", False),
             ("Кравченко Дмитро", "Кравченка Дмитра", True), ("Воронін Олексій Петрович", "Вороніна Олексія Петровича", True), ("Кацуба", "Кацуби", True)]
    for q, t, exp in tests:
        got = name_matches(q, t)
        print("OK " if got == exp else "FAIL", q, "|", t, "->", got)


OPF_PREFIX = re.compile(r"^(тов|тзов|пат|прат|ат|тдв|дп|кп|пп|фг|го|бф|кс|аб|ооо|зао|оао|ао|llc|ltd|inc)\b\.?\s*", re.I)


def is_org(q: str) -> bool:
    return bool(OPF_PREFIX.match(q.strip()) or re.search(r"[«»\"]", q))


def org_regex(q: str, pg: bool = False) -> str:
    """Company names: drop the legal-form prefix and quotes, match the core name as a phrase (any quotes/spacing between
    words), case-insensitive. «ТОВ "ГАЗ-МДС"» -> \\mГАЗ-МДС\\M. A core shorter than 3 letters returns a never-matching pattern."""
    core = OPF_PREFIX.sub("", re.sub(r"[«»\"'’“”]", " ", q)).strip()
    core = OPF_PREFIX.sub("", core).strip()
    words = [re.escape(w) for w in core.split() if w]
    if not words or len("".join(words)) < 3:
        return r"(?!x)x"
    b0, b1 = (r"\m", r"\M") if pg else (r"\b", r"\b")
    return b0 + r"[\s«»\"'’“”]{1,3}".join(words) + b1
