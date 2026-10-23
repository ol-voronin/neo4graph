// Query text helpers shared by /api/search and the chat tools: Russian -> Ukrainian spelling variants, crude stemming,
// word regexes that tolerate и/і/ы, е/є/э and the і/о alternation (Харків/Харкова), oblast detection, dates.
export const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();

// first names whose Ukrainian form is not a letter-by-letter mapping (both forms are kept)
const NAMES = { александр: "олександр", алексей: "олексій", андрей: "андрій", сергей: "сергій", евгений: "євген", дмитрий: "дмитро",
  николай: "микола", владимир: "володимир", виктор: "віктор", игорь: "ігор", юрий: "юрій", григорий: "григорій", михаил: "михайло",
  павел: "павло", петр: "петро", пётр: "петро", иван: "іван", василий: "василь", анатолий: "анатолій", виталий: "віталій",
  валерий: "валерій", геннадий: "геннадій", аркадий: "аркадій", олег: "олег", илья: "ілля", кирилл: "кирило", федор: "федір",
  фёдор: "федір", тимур: "тімур", елена: "олена", ольга: "ольга", наталья: "наталія", татьяна: "тетяна", екатерина: "катерина",
  ирина: "ірина", юлия: "юлія", анна: "ганна", мария: "марія", светлана: "світлана", людмила: "людмила" };

// one word -> spellings to try, the input first. Russian-only letters are mapped; every «и» may be Ukrainian «і» or «и».
function wordVariants(w) {
  w = w.toLowerCase();
  const base = w.replace(/ё/g, "е").replace(/ъ/g, "'").replace(/э/g, "е").replace(/ы/g, "и");
  const out = new Set([w, base]);
  if (NAMES[w]) out.add(NAMES[w]);
  if (NAMES[base]) out.add(NAMES[base]);
  const pos = [...base].map((c, i) => (c === "и" ? i : -1)).filter((i) => i >= 0).slice(0, 3);
  const combos = [];
  for (let m = 1; m < 1 << pos.length; m++) {
    const a = [...base];
    pos.forEach((p, j) => { if (m & (1 << j)) a[p] = "і"; });
    combos.push([a.join(""), pos.length - pos.filter((_, j) => m & (1 << j)).length]);
  }
  // all-«і» first: Миндич -> Міндіч, then the mixed ones
  combos.sort((x, y) => x[1] - y[1]).forEach(([v]) => out.add(v));
  if (/^е/.test(base)) out.add("є" + base.slice(1));            // Евгений -> Євген…
  if (/ей$/.test(base)) out.add(base.replace(/ей$/, "ій"));     // Сергей -> Сергій
  return [...out];
}

// phrase -> up to `max` spellings, the input itself first (lower case, spaces collapsed)
export function ukVariants(q, max = 4) {
  const words = norm(q).toLowerCase().split(" ").filter(Boolean);
  if (!words.length) return [];
  const lists = words.map((w) => wordVariants(w).slice(0, 4));
  let combos = [[[], 0]];
  for (const l of lists) combos = combos.flatMap(([p, s]) => l.map((v, i) => [[...p, v], s + i]));
  const seen = new Set(), out = [];
  for (const [p] of combos.sort((a, b) => a[1] - b[1])) {
    const v = p.join(" ");
    if (!seen.has(v)) { seen.add(v); out.push(v); }
    if (out.length >= max) break;
  }
  return out;
}

const STOP = new Set(`і й и та да а але но що что як как які какие який какой яка какая яке какое хто кто де где коли когда чи ли
  про о об обо по в у во на з с із зі зо со за до від от для при після после перед між между це это цей этот ця эта ці эти той тот те
  був була було були был была было были є есть буде будет мені мне нам нас вони они він он вона она його его її ее их їх всі все всё усе
  ще еще вже уже також также тоже дуже очень там тут вчора вчера сьогодні сегодня завтра позавчора позавчера напередодні накануне
  писали писал писала пишуть пишут писав відомо известно знайди найди знайти найти покажи покажіть показать розкажи расскажи
  дай дайте щодо касательно стосовно справа справи справу справі справою дело дела делу деле делом інформацію информацию інформація
  информация інфо инфо весь вся цього этого цим этим тому которые котрі які яких каких котрий который мене меня можна можно треба нужно
  чем чим ніж где-то хтось кто-то щось что-то шо`.split(/\s+/).filter(Boolean));
export const isStop = (w) => STOP.has(w.toLowerCase());

// crude Ukrainian/Russian stem: drop one inflection ending, keep at least 4 letters
const ENDINGS = ["ами", "ями", "ові", "еві", "єві", "ого", "ому", "ими", "іми", "ыми", "ій", "ий", "ой", "ою", "ею", "єю", "ом", "ем", "ям",
  "ам", "ах", "ях", "ів", "їв", "ов", "ев", "ої", "ая", "яя", "ую", "юю", "ые", "ие", "і", "ї", "и", "ы", "а", "я", "у", "ю", "е", "є", "о", "ь"];
export function stem(w) {
  w = w.toLowerCase();
  if (w.length < 5 || /\d/.test(w)) return w;
  for (const e of ENDINGS) if (w.endsWith(e) && w.length - e.length >= 4) return w.slice(0, -e.length);
  return w;
}

// Words worth searching for: stop words and 1-2 letter tokens dropped, stems kept (digits and case numbers intact)
// -ов/-ев/-ів/-їв are mostly surname suffixes in a query, not endings: «Андреев» -> «андре» matched every «Андрей» (wordRe's
// alternating vowel already covers Андреєв / Андреева / Харкова)
const keepSurname = (w) => (/^[^\d]{3,}(ов|ев|єв|ів|їв)$/i.test(w) ? w.toLowerCase() : stem(w));
export function queryWords(q) {
  return norm(q).replace(/[«»"“”„()?!,.:;]+/g, " ").split(" ")
    .filter((w) => w && !isStop(w) && (w.length >= 3 || /^\d+$/.test(w))).map(keepSurname).filter(Boolean);
}

// Postgres ARE (no backslashes, no locale dependence): a word starting with `w` in any case and in Russian or Ukrainian
// spelling. Letter classes: и/і/ы/ї, е/є/э, ё=е, г/ґ, apostrophes; the last vowel of a stem may alternate (Харків/Харкова, Київ/Києва).
const CLS = { и: "иіыїИІЫЇ", і: "иіыїИІЫЇ", ы: "иіыїИІЫЇ", ї: "иіыїИІЫЇ", е: "еєэёЕЄЭЁ", є: "еєэёЕЄЭЁ", э: "еєэёЕЄЭЁ", ё: "еєэёЕЄЭЁ", г: "гґГҐ", ґ: "гґГҐ" };
const ALT = "оеєіїиыОЕЄІЇИЫ";
const VOW = /[аоуеєиіїыэюяё]/;
export function wordRe(w) {
  const a = [...w.toLowerCase()];
  // the alternating vowel: the last vowel when only consonants follow it (харков -> харк[оі..]в), only in stems of 4+ letters
  let alt = -1;
  if (a.length >= 4 && !VOW.test(a.at(-1))) for (let i = a.length - 2; i >= 1; i--) if (VOW.test(a[i])) { if ("оеєіїиы".includes(a[i])) alt = i; break; }
  const body = a.map((c, i) => {
    if (i === alt) return `[${ALT}]`;
    if (CLS[c]) return `[${CLS[c]}]`;
    if (/['’ʼ`]/.test(c) || c === "ь") return "[ьЬ'’ʼ`]?";
    // Russian spelling keeps a soft sign where Ukrainian has none (Харьков / Харків)
    if (/[бвгґджзклмнпрстфхцчшщ]/.test(c)) return `[${c}${c.toUpperCase()}][ьЬ]?`;
    if (/[a-zа-яґ]/.test(c)) return `[${c}${c.toUpperCase()}]`;
    return /[0-9]/.test(c) ? c : "[" + c.replace(/[\]\\^-]/g, "") + "]";
  }).join("");
  return `(?:^|[^а-яА-ЯіїєґІЇЄҐёЁa-zA-Z0-9])${body}`;
}

// ---- oblasts: slug (alerts.region_id, extracted_events.region) <- stems in Ukrainian, Russian and Latin ----
export const OBLASTS = [
  ["kharkivska", "Харківська", "харк|харьк|kharkiv|kharkov"], ["kyivska", "Київська", "київщин|київськ|киевск|kyivska"],
  ["kyiv-city", "м. Київ", "київ|києв|киев|kyiv|kiev"], ["dnipropetrovska", "Дніпропетровська", "дніпр|днепр|dnipr|кривий ріг|кривого рогу|кривой рог|кривом рогу"],
  ["odeska", "Одеська", "одес|odes"], ["zaporizka", "Запорізька", "запор|zaporiz"], ["lvivska", "Львівська", "львів|львов|lviv"],
  ["sumska", "Сумська", "сумщин|сумськ|сумск|суми|сумы|сумах|сумам|sumy"], ["chernihivska", "Чернігівська", "черніг|чернигов|chernihiv"],
  ["poltavska", "Полтавська", "полтав|кременчу|poltav"], ["mykolaivska", "Миколаївська", "миколаїв|николаев|mykolai"],
  ["khersonska", "Херсонська", "херсон|kherson"], ["donetska", "Донецька", "донец|донеч|краматор|слов'янськ|славянск|покровськ|donetsk"],
  ["luhanska", "Луганська", "луган|luhansk"], ["vinnytska", "Вінницька", "вінниц|винниц|vinnyts"], ["volynska", "Волинська", "волин|луцьк|луцк"],
  ["zhytomyrska", "Житомирська", "житомир|zhytomyr"], ["zakarpatska", "Закарпатська", "закарпат|ужгород"],
  ["ivano-frankivska", "Івано-Франківська", "франків|франковск|прикарпат"], ["kirovohradska", "Кіровоградська", "кіровоград|кировоград|кропивниц"],
  ["rivnenska", "Рівненська", "рівн|ровно|ровен|rivne"], ["ternopilska", "Тернопільська", "терноп|ternopil"],
  ["khmelnytska", "Хмельницька", "хмельниц|khmelnyts"], ["cherkaska", "Черкаська", "черкас|cherkas"], ["chernivetska", "Чернівецька", "чернівц|черновц|буковин"],
];
// first oblast named in the text (slug passes through)
export function oblastOf(s) {
  const t = String(s || "").toLowerCase();
  if (!t) return null;
  const hit = OBLASTS.find(([slug]) => slug === t);
  if (hit) return hit;
  return OBLASTS.find(([, , re]) => new RegExp("(^|[^а-яіїєґё])(" + re + ")", "u").test(t)) || null;
}
// ARE for posts that mention the oblast or its centre (any case, ua/ru spelling); stems are lower case, so match lower(text)
export const oblastTextRe = (o) => "(?:^|[^а-яіїєґё])(?:" + o[2].replace(/'/g, "['’ʼ]?") + ")";

// ---- dates in the Kyiv calendar ----
export const kyivToday = (now = new Date()) => new Date(now.toLocaleString("en-US", { timeZone: "Europe/Kyiv" })).toISOString().slice(0, 10);
export const addDays = (d, n) => { const x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };
const MONTHS = ["січ|янв", "лют|фев", "берез|март|мар", "квіт|апр", "трав|мая|май", "черв|июн", "лип|июл", "серп|авг", "верес|сент|сен", "жовт|октя|окт", "листоп|нояб", "груд|дек"];
// the day a question is about: "вчора", "позавчора", "сьогодні", "21 жовтня", "21.10", ISO; null when none
export function dateOf(text, today = kyivToday()) {
  const t = String(text || "").toLowerCase();
  let m;
  if ((m = t.match(/(20\d\d)-(\d\d)-(\d\d)/))) return m[0];
  if ((m = t.match(/(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(20\d\d|\d\d))?(?!\d)/))) {
    const y = m[3] ? (m[3].length === 2 ? "20" + m[3] : m[3]) : today.slice(0, 4);
    return `${y}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`;
  }
  if ((m = t.match(/(?<!\d)(\d{1,2})\s+([а-яіїє]+)/))) {
    const mi = MONTHS.findIndex((x) => new RegExp("^(" + x + ")").test(m[2]));
    if (mi >= 0) return `${today.slice(0, 4)}-${String(mi + 1).padStart(2, "0")}-${m[1].padStart(2, "0")}`;
  }
  if (/позавчора|позавчера/.test(t)) return addDays(today, -2);
  if (/вчора|вчера|минулої доби|прошлой ночью|минулої ночі|цієї ночі|этой ночью/.test(t)) return addDays(today, -1);
  if (/сьогодні|сегодня|зараз|сейчас/.test(t)) return today;
  return null;
}
