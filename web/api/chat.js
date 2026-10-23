import { sql, json, query } from "./_db.js";
import { resolve, dossier, corpus, caseCard, strikes, rhetoricBefore, withTimeout, CAUSE_ANY, CASE_FULL, FOP_RE } from "./_intent.js";
import { kyivToday } from "./_text.js";
import { connectMany } from "./_connect.js";
import { nameLink, countCandidates, identityKey } from "./_identity.js";

// POST /api/chat {messages:[{role,content}]} -> answer + structured results + graph. Claude Haiku with tools over the database;
// before the model runs, the server resolves what the question names and gathers the obvious data (one model step saved).
const SYSTEM = (today) => `Ти — асистент дослідницької бази «Rhetoric Graph». Сьогодні ${today} (Київ). У базі:
1) корпус ~120 тис. постів Telegram-каналів і RSS: українські (офіційні — Повітряні сили, ОВА, Синєгубов; медіа; антикорупційні — НАБУ, САП, Bihus) і російські (Міноборони РФ, МЗС, Кремль, ТАСС, воєнкори: Рибарь, Два майора тощо), з LLM-мітками погроз (threat_level), типом події й наративом;
2) удари: загальнонаціональні хвилі (Повітряні сили), щоденні зведення Харківської ОВА, зведення по областях, витягнуті з постів події (ракети, дрони, КАБ, жертви), повітряні тривоги по областях;
3) заяви сторін (погрози, обіцянки, прогнози) з перевіркою, чи збулися;
4) антикорупційний шар: особи, компанії, справи, активи з постів; рішення судів (ЄДРСР) і статистика судів; ЄДР (компанії, засновники, підписанти), ФОП, декларації НАЗК, реєстр боржників, ProZorro; згадки в інтернеті; граф звʼязків між усім цим.
Правила:
- Будь-яке назване в питанні (людина, компанія, ФОП, справа, номер справи, ЄДРПОУ/РНОКПП, місто/область, канал) — спершу resolve, далі entity_dossier для знайденого id. Запит російською теж: resolve сам дає українські написання (Миндич → Міндіч).
- Удари: strikes(region, date_from, date_to). «Що писали російські канали до/напередодні» — rhetoric_before(date = день удару, days 1–3, region). «Вчора» = день перед сьогоднішнім.
- Теми й слова в постах — search_corpus (кожне слово має бути в пості; форми слова й написання враховано).
- Ніколи не кажи, що в базі немає теми, доки не пошукав інструментами; якщо порожньо — скажи, що саме шукав і де, і запропонуй enqueue.
- Якщо в повідомленні є «Сервер уже виконав», використай ці результати і не повторюй ті самі виклики.
- Відповідай мовою користувача, стисло й конкретно: дати, джерела (канал, суд, реєстр), числа, номери справ, посилання. Кожен факт — з джерелом із результатів інструментів. Не вигадуй.
- Справи й суди: номер справи → get_case; статистика судів → court_stats (сімейні: category_re "сімейн|шлюб|аліме|батьківськ|усиновл"; у кримінальних багато ухвал слідчих суддів, тому частка вироків низька). Категорії ЄДРСР — українські назви ("земел"). «За 25-й рік» = 2025.
- Питання «як повʼязаний / яке відношення / при чому / звʼязок між» X і Y (і Z) — обовʼязково connect(items: [X, Y, Z]) з назвами як у запиті (номер справи, прізвище, компанія). Відповідь — не перелік ухвал, а пояснення звʼязку: 1) прямі звʼязки — де саме X названий у справі чи поруч з Y (документ, дата, речення, роль: підозрюваний, свідок, адвокат, суддя, директор компанії-учасника); 2) опосередковані — через компанію, людину-посередника, подію чи інше провадження, кожен крок ланцюжка окремо: «X — засновник ТОВ А (ЄДР) → ТОВ А названа в ухвалі від … у справі Y»; 3) чого не знайдено і чому (наприклад, приватних осіб реєстр знеособлює як ОСОБА_n, тексти не завантажені — запропонуй get_case/enqueue). Позначай силу кожного звʼязку (прямий / через посередника / лише збіг прізвища) і не роби висновків про вину чи причетність понад те, що кажуть документи. Деталі самої справи (форми рішень, суди) — лише коротко, як контекст.
- ФОП: find_fop; збіг за імʼям може бути однофамільцем — кажи це. Не зводь ФОП, суди й борги в одну історію про людину; не роби висновків про вину. Збіг риторики й ударів у часі не доводить причинності.`;

const TOOLS = [
  { name: "resolve", description: "Що названо в запиті: нормалізує написання (рос.→укр., відмінки), визначає вид (справа, ЄДРПОУ, РНОКПП, ФОП, особа, компанія, місце, канал, удари, риторика, дата) і повертає кандидатів з id вузлів графа та best.",
    input_schema: { type: "object", properties: { q: { type: "string" } }, required: ["q"] } },
  { name: "entity_dossier", description: "Усе про один обʼєкт за id з resolve (person:…, org:…, case:…, registry:<ЄДРПОУ>, fop:…, place:<slug>, channel:<id>, post:<id>): сусіди в графі, справи й рішення судів, пости (усі написання, нові спершу), заяви, реєстри, веб-згадки.",
    input_schema: { type: "object", properties: { id: { type: "string" } }, required: ["id"] } },
  { name: "search_corpus", description: "Пошук постів за словами: кожне слово має бути в пості (будь-яка форма й рос./укр. написання), нові спершу; more=true — є ще. Фільтри: дати, країна каналу (UA/RU), канал, область.",
    input_schema: { type: "object", properties: { q: { type: "string" }, since: { type: "string", description: "YYYY-MM-DD" }, until: { type: "string", description: "YYYY-MM-DD, не включно" },
      country: { type: "string", enum: ["UA", "RU"] }, source: { type: "string", description: "id каналу, напр. tg:rybar" }, region: { type: "string", description: "область чи місто" }, limit: { type: "integer", default: 15 } }, required: ["q"] } },
  { name: "strikes", description: "Удари за дні: загальнонаціональні хвилі (ракети/дрони/збиті), по області (зведення ОВА, витягнуті події з постами-джерелами, жертви), Харків окремо, повітряні тривоги по області. Без region — усі області.",
    input_schema: { type: "object", properties: { region: { type: "string", description: "місто/область або slug (kharkivska)" }, date_from: { type: "string", description: "YYYY-MM-DD" }, date_to: { type: "string" } }, required: ["date_from"] } },
  { name: "rhetoric_before", description: "Що писали російські канали за days днів до дати (не включно; include_day=true — разом із днем): пости з мітками погроз, згадки області, по каналах, порівняння з середнім за 14 днів.",
    input_schema: { type: "object", properties: { date: { type: "string", description: "YYYY-MM-DD, день події" }, days: { type: "integer", default: 1 }, region: { type: "string" }, include_day: { type: "boolean" } }, required: ["date"] } },
  { name: "search_decisions", description: "Пошук судових рішень у базі (індекс ЄДРСР). Повертає групування по справах або список рішень.",
    input_schema: { type: "object", properties: {
      year: { type: "integer" }, category: { type: "string", description: "фрагмент назви категорії, напр. 'земел'" },
      court: { type: "string", description: "фрагмент назви суду" }, judge: { type: "string" }, form: { type: "string", description: "Вирок|Ухвала|Рішення|Постанова" },
      cause_num: { type: "string" }, justice_kind: { type: "string", description: "Кримінальне|Цивільне|Адміністративне|Господарське" },
      group_by_case: { type: "boolean", default: true }, limit: { type: "integer", default: 30 } } } },
  { name: "list_categories", description: "Які категорії справ і суди є в індексі (з кількістю), щоб зрозуміти покриття.", input_schema: { type: "object", properties: { year: { type: "integer" }, like: { type: "string" } } } },
  { name: "search_entities", description: "Підрядковий пошук у сирих таблицях: особи, компанії (з ЄДРПОУ), справи й активи з антикорупційних постів, ЄДР, веб. Краще спершу resolve.", input_schema: { type: "object", properties: { q: { type: "string" } }, required: ["q"] } },
  { name: "search_statements", description: "Заяви сторін (загрози, обіцянки, прогнози) з вердиктом резолвера і послужний список акторів.", input_schema: { type: "object", properties: { actor_side: { type: "string" }, q: { type: "string" }, limit: { type: "integer", default: 20 } } } },
  { name: "get_case", description: "Картка справи за номером (напр. 991/1234/21): усі рішення в базі — дати, форми, суди, судді, посилання на тексти. Якщо рішень немає — ставить справу в чергу.",
    input_schema: { type: "object", properties: { cause_num: { type: "string" } }, required: ["cause_num"] } },
  { name: "find_fop", description: "Запис ЄДР про ФОП за повним імʼям (прізвище імʼя по батькові): стан, дата реєстрації, припинення. Якщо ще не шукали — ставить пошук у чергу (результат за 5–10 хв).",
    input_schema: { type: "object", properties: { name: { type: "string" } }, required: ["name"] } },
  { name: "court_stats", description: "Статистика ЄДРСР за всіма видами проваджень: скільки рішень і справ за роками, видом, категорією, областю, формою; частка справ із вироком / рішенням / судовим наказом, медіана тривалості в днях.",
    input_schema: { type: "object", properties: {
      year_from: { type: "integer" }, year_to: { type: "integer" },
      justice_kind: { type: "string", description: "Цивільне | Кримінальне | Адміністративне | Господарське | Адмінправопорушення" },
      category_re: { type: "string", description: "регулярний вираз по назві категорії, напр. 'сімейн|шлюб|аліме' або 'крадіжк'" },
      region: { type: "string", description: "фрагмент назви області, напр. 'Харків'" },
      form: { type: "string", description: "Вирок | Рішення | Ухвала | Постанова | Судовий наказ" } } } },
  { name: "connect", description: "Як повʼязані 2–4 обʼєкти (особа, компанія, справа за номером, ЄДРПОУ): ланцюжки в графі (≤4 кроки, кожен крок з типом звʼязку й доказом), де особу названо в текстах рішень справи (речення з роллю), компанії особи (ЄДР, декларації), що фігурують у справі, посередники (люди, компанії, справи, події в межах двох кроків від обох), пости, витягнуті події й рішення судів, де названо обох; для 3+ — ще спільні сусіди всіх. items — як у запиті: «932/2641/25», «Соляник», «ТОВ Ромашка».",
    input_schema: { type: "object", properties: { items: { type: "array", items: { type: "string" }, minItems: 2, maxItems: 4 } }, required: ["items"] } },
  { name: "enqueue", description: "Поставити в чергу роботу з реєстрами: kind=case (номер справи), person, org, edrpou, text, web (згадки в інтернеті), category_index (query у форматі 'РІК|фрагмент категорії|форма?' напр. '2025|земел' — індексує всі такі рішення року).",
    input_schema: { type: "object", properties: { query: { type: "string" }, kind: { type: "string", enum: ["case", "person", "org", "edrpou", "text", "web", "category_index", "fop"] } }, required: ["query", "kind"] } },
];

async function enqueue(q, kind) {
  const open = await sql`SELECT id, status FROM query_queue WHERE query = ${q} AND kind = ${kind} AND status IN ('queued','running') LIMIT 1`;
  if (open.length) return { queued: true, existing: true, ...open[0] };
  const row = await sql`INSERT INTO query_queue(query, kind) VALUES (${q}, ${kind}) RETURNING id, status, created_at`;
  return { queued: true, ...row[0], note: "Черга обробляється кожні 5 хв; пошук ФОП — 5–10 хв, справа чи індексація зрізу року — 5–20 хв." };
}

async function runTool(name, a) {
  if (name === "resolve") return resolve(a.q || "");
  if (name === "entity_dossier") return dossier(a.id || "", { enqueue });
  if (name === "search_corpus") return corpus({ q: a.q, since: a.since, until: a.until, country: a.country, source: a.source, region: a.region, limit: a.limit });
  if (name === "strikes") return strikes(a);
  if (name === "rhetoric_before") return rhetoricBefore(a);
  if (name === "search_decisions") {
    const lim = Math.min(100, a.limit || 30);
    const where = [];
    const params = [];
    const add = (cond, v) => { params.push(v); where.push(cond.replace("?", "$" + params.length)); };
    if (a.year) add("year = ?", a.year);
    if (a.category) add("category ILIKE ?", "%" + a.category + "%");
    if (a.court) add("court ILIKE ?", "%" + a.court + "%");
    if (a.judge) add("judge ILIKE ?", "%" + a.judge + "%");
    if (a.form) add("judgment_form ILIKE ?", "%" + a.form + "%");
    if (a.cause_num) add("cause_num = ?", a.cause_num);
    if (a.justice_kind) add("justice_kind ILIKE ?", "%" + a.justice_kind + "%");
    const w = where.length ? "WHERE " + where.join(" AND ") : "";
    if (a.group_by_case !== false) {
      const rows = await query(`SELECT cause_num, max(court) court, count(*)::int n, min(adjudication_date)::text d0, max(adjudication_date)::text d1, max(category) category, string_agg(DISTINCT judgment_form, ', ') forms FROM court_decisions ${w} GROUP BY cause_num ORDER BY n DESC LIMIT ${lim}`, params);
      const tot = await query(`SELECT count(*)::int n, count(DISTINCT cause_num)::int cases FROM court_decisions ${w}`, params);
      return { total_decisions: tot[0].n, total_cases: tot[0].cases, cases: rows };
    }
    return { decisions: await query(`SELECT doc_id, cause_num, court, judgment_form, category, adjudication_date::text adjudication_date, judge, doc_url FROM court_decisions ${w} ORDER BY adjudication_date DESC LIMIT ${lim}`, params) };
  }
  if (name === "list_categories") {
    const rows = await sql`SELECT year, category, count(*)::int n FROM court_decisions WHERE (${a.year || null}::int IS NULL OR year = ${a.year || null}) AND (${a.like || ""} = '' OR category ILIKE ${"%" + (a.like || "") + "%"}) GROUP BY 1,2 ORDER BY n DESC LIMIT 40`;
    const courts = await sql`SELECT year, court, count(*)::int n FROM court_decisions GROUP BY 1,2 ORDER BY n DESC LIMIT 15`;
    return { categories: rows, courts };
  }
  if (name === "search_entities") {
    const pat = "%" + a.q + "%";
    return {
      persons: await sql`SELECT name_norm, string_agg(DISTINCT role, ',') roles, count(*)::int n, max(position) position FROM ee_persons WHERE name_norm ILIKE ${pat} OR position ILIKE ${pat} GROUP BY 1 LIMIT 15`,
      orgs: await sql`SELECT name, max(edrpou) edrpou, count(*)::int n FROM ee_orgs WHERE name ILIKE ${pat} GROUP BY 1 LIMIT 15`,
      edr: await sql`SELECT edrpou, short_name, stan, founders, signers FROM edr_orgs WHERE name ILIKE ${pat} OR short_name ILIKE ${pat} OR founders::text ILIKE ${pat} OR signers::text ILIKE ${pat} LIMIT 10`,
      cases: await sql`SELECT nickname, agency, stage, summary FROM ee_cases WHERE nickname ILIKE ${pat} OR summary ILIKE ${pat} LIMIT 10`,
      assets: await sql`SELECT type, description, linked_person FROM ee_assets WHERE description ILIKE ${pat} OR linked_person ILIKE ${pat} LIMIT 10`,
      web: await sql`SELECT title, url, snippet FROM web_mentions WHERE query ILIKE ${pat} OR title ILIKE ${pat} LIMIT 10`.catch(() => []),
    };
  }
  if (name === "search_statements") {
    const pat = "%" + (a.q || "") + "%";
    const rows = await sql`SELECT ts_published, actor_side, actor_name, stype, quote, predicted_event, target_region, status, resolver_note FROM statements
      WHERE (${a.actor_side || ""} = '' OR actor_side = ${a.actor_side || ""}) AND (${a.q || ""} = '' OR quote ILIKE ${pat} OR predicted_event ILIKE ${pat} OR actor_name ILIKE ${pat})
      ORDER BY ts_published DESC LIMIT ${Math.min(50, a.limit || 20)}`;
    const track = await sql`SELECT * FROM actor_track_record ORDER BY n_resolved DESC LIMIT 30`;
    return { statements: rows, track_record: track };
  }
  if (name === "get_case") {
    const num = (a.cause_num || "").trim();
    if (!CASE_FULL.test(num)) return { error: "номер справи має вигляд 991/1234/21" };
    return caseCard(num, enqueue);
  }
  if (name === "find_fop") {
    const q = (a.name || "").replace(/ФОП/gi, "").replace(/\s+/g, " ").trim();
    if (q.split(" ").length < 2) return { error: "потрібне повне імʼя: прізвище та імʼя (краще з по батькові)" };
    const rows = await sql`SELECT title, snippet, payload FROM registry_hits WHERE source = 'edr_fop' AND lower(query) = lower(${q}) ORDER BY title LIMIT 20`;
    if (rows.length) return { found: rows.length, records: rows.map((r) => r.payload), note: "Збіг за імʼям: це можуть бути різні люди (однофамільці)." };
    return { found: 0, ...(await enqueue(q, "fop")) };
  }
  if (name === "court_stats") {
    const P = [a.year_from || 2021, a.year_to || a.year_from || 2100, a.justice_kind || "", (a.category_re || "").slice(0, 200), "%" + (a.region || "") + "%", a.form || ""];
    const W = "year BETWEEN $1 AND $2 AND ($3 = '' OR justice_kind = $3) AND ($4 = '' OR category ~* $4) AND ($5 = '%%' OR region ILIKE $5)";
    const F = " AND ($6 = '' OR form = $6)";
    const q = (text) => query(text, P);
    const [byYear, byForm, topCat, topReg, cases] = await Promise.all([
      q(`SELECT year, sum(n)::int decisions FROM edrsr_stats WHERE ${W}${F} GROUP BY 1 ORDER BY 1`),
      q(`SELECT form, sum(n)::int decisions FROM edrsr_stats WHERE ${W} AND $6::text = $6::text GROUP BY 1 ORDER BY 2 DESC LIMIT 8`),
      q(`SELECT justice_kind, category, sum(n)::int decisions FROM edrsr_stats WHERE ${W}${F} GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12`),
      q(`SELECT region, sum(n)::int decisions FROM edrsr_stats WHERE ${W}${F} GROUP BY 1 ORDER BY 2 DESC LIMIT 8`),
      q(`SELECT year, sum(cases)::int cases, sum(with_verdict)::int with_verdict, sum(with_decision)::int with_decision, sum(with_court_order)::int with_court_order,
                round((sum(median_days * cases) FILTER (WHERE median_days IS NOT NULL) / nullif(sum(cases) FILTER (WHERE median_days IS NOT NULL), 0))::numeric, 0) median_days_weighted
         FROM edrsr_case_stats WHERE ${W} AND $6::text = $6::text GROUP BY 1 ORDER BY 1`),
    ]);
    if (!byYear.length) return { found: 0, note: "У статистиці немає таких рядків: перевірте рік або фрагмент категорії/області." };
    return { by_year: byYear, by_form: byForm, top_categories: topCat, top_regions: topReg, cases_by_year: cases,
      note: "Справа = номер справи в межах року; median_days_weighted — зважене середнє медіан тривалості між першим і останнім рішенням (наближення). Дані: відкриті дампи ЄДРСР (data.gov.ua), без імен сторін." };
  }
  if (name === "connect") return connectMany(Array.isArray(a.items) ? a.items : [a.a, a.b, a.c].filter(Boolean));
  if (name === "enqueue") return enqueue((a.query || "").trim(), a.kind);
  return { error: "unknown tool" };
}

// one tool call: 12 s cap, result for the model (without the page-only graph), trace entry for the page
async function call(name, input, trace) {
  const t1 = Date.now();
  const out = await withTimeout(runTool(name, input || {}), name === "connect" ? 22000 : 12000).catch((e) => ({ error: String(e.message || e) }));
  const ms = Date.now() - t1;
  console.log("tool", name, ms, "ms");
  const { _graph, ...forModel } = out && typeof out === "object" ? out : { value: out };
  trace.push({ tool: name, input, ms, summary: summarize(forModel), _graph });
  return forModel;
}

// ---- graph payload: what the tools found, as nodes/edges with the same ids as /api/graph, so the page can merge and focus ----
const lc = (x) => String(x || "").replace(/\s+/g, " ").trim().toLowerCase();
const clip = (s, n) => (s && s.length > n ? s.slice(0, n) + "…" : s || "");
function toGraph(trace) {
  const nodes = new Map(), edges = [], seen = new Set();
  let focus = null, prio = 9;
  const want = (id, p) => { if (id && p < prio) { prio = p; focus = id; } };
  const node = (id, type, label, attrs = {}) => {
    const x = nodes.get(id);
    if (!x) nodes.set(id, { id, type, label, ...attrs }); else for (const [k, v] of Object.entries(attrs)) if (x[k] == null && v != null) x[k] = v;
    want(id, 8);
    return id;
  };
  const edge = (source, target, rel, attrs = {}) => { const k = source + "|" + target + "|" + rel; if (source === target || seen.has(k)) return; seen.add(k); edges.push({ source, target, rel, msgs: [], ...attrs }); };
  const post = (p, id = p.id) => node("post:" + id, "post", clip(p.snippet || p.details || "", 70), { src: p.source, date: p.date, url: p.url, text: p.snippet || p.details });
  const chan = (sid, name) => node("channel:" + sid, "channel", name || sid);
  const posted = (p, id) => { const pid = post(p, id); if (p.source) edge(pid, chan(p.source, p.src), "published_in", { date: p.date }); return pid; };
  const place = (r) => node("place:" + r.id, "place", r.name);
  const strikeNodes = (o, p) => {
    if (!o || o.error) return;
    const pl = o.region ? place(o.region) : null;
    if (pl) want(pl, p);
    for (const e of o.events || []) { const pid = posted({ ...e, snippet: `${e.date}: ${e.details || "удар"} (ракети ${e.missiles}, дрони ${e.drones}, КАБ ${e.guided_bombs})` }, e.message_id); if (pl && (!e.region || e.region === o.region.id)) edge(pid, pl, "located_in", { date: e.date }); }
    if (Array.isArray(o.ua_posts)) for (const x of o.ua_posts) { const pid = posted(x); if (pl) edge(pid, pl, "located_in", { date: x.date }); }
  };
  for (const t of trace) {
    const o = t.summary || {};
    if (o.error) continue;
    if (t.tool === "resolve") {
      for (const c of (o.candidates || []).slice(0, 6)) node(c.id, c.type, c.label, c.sub ? { sub: c.sub } : {});
      want(o.best, o.best?.startsWith("place:") ? 3 : 1);
    }
    if (t.tool === "connect" && t._graph) {
      for (const n of t._graph.nodes || []) node(n.id, n.type, n.label, n);
      for (const e of t._graph.edges || []) edge(e.source, e.target, e.rel, e);
      want(t._graph.focus, 0);
    }
    if (t.tool === "entity_dossier") {
      const id = o.id, g = t._graph;
      if (!id) continue;
      for (const n of g?.nodes || []) node(n.id, n.type, n.label, n);
      for (const e of g?.edges || []) edge(e.source, e.target, e.rel, e);
      node(id, o.node?.type || id.slice(0, id.indexOf(":")), o.node?.label || o.label || id);
      want(id, 0);
      for (const p of o.posts?.items || []) edge(id, posted(p), "mentioned_in", { date: p.date, src: p.source });
      for (const d of o.decisions || []) edge(id, node("decision:" + d.doc_id, "decision", `${d.f || "Рішення"} ${d.d || ""}`, { doc_id: d.doc_id, date: d.d, court: d.court, url: d.doc_url }), "court_register_mention");
      for (const d of o.court?.timeline || []) edge(node("decision:" + d.doc_id, "decision", `${d.f || "Рішення"} ${d.d || ""}`, { doc_id: d.doc_id, form: d.f, date: d.d, court: d.court, judge: d.judge, url: d.doc_url, status: "1" }), id, "decision_of", { date: d.d });
      for (const m of o.media_cases || []) edge(id, node("case:" + lc(m.cause_num), "case", "Справа " + m.cause_num, { ref: m.cause_num }), "named_by_media", { detail: `${m.role}: «${m.quote}» ${m.url}` });
      for (const w of o.web || []) edge(id, node("web:" + w.url, "web", clip(w.title || w.domain || w.url, 80), { url: w.url, text: w.snippet, src: w.domain }), "web_mention");
      for (const r of o.edr_roles || []) edge(id, node("registry:" + r.edrpou, "registry", `ЄДР ${r.edrpou} · ${r.short_name || ""}`, { edrpou: r.edrpou, stan: r.stan }), "related");
      strikeNodes(o.strikes, 0);
      if (id.startsWith("place:")) for (const p of o.posts?.items || []) edge(posted(p), id, "located_in", { date: p.date });
    }
    if (t.tool === "strikes") strikeNodes(o, 2);
    if (t.tool === "rhetoric_before") {
      const pl = o.region ? place(o.region) : null;
      // the posts that matter on the graph: naming the oblast or labelled as threats, then at most a few with threat words
      let extra = 4;
      for (const p of o.posts || []) {
        if (!p.mentions_region && !(p.threat_level >= 2) && !(p.threat_words && extra-- > 0)) continue;
        const pid = posted(p);
        if (pl && p.mentions_region) edge(pid, pl, "located_in", { date: p.date });
      }
      if (pl) want(pl, 2);
    }
    if (t.tool === "search_corpus") for (const p of o.items || []) posted(p);
    if (t.tool === "get_case" && o.found) {
      const num = t.input.cause_num, cid = node("case:" + lc(num), "case", "Справа " + num, { ref: num });
      want(cid, 0);
      for (const d of o.timeline || []) {
        if (!d.doc_id) continue;
        const date = dt(d.d), did = node("decision:" + d.doc_id, "decision", `${d.f || "Рішення"} ${date}`, { doc_id: d.doc_id, form: d.f, date, court: d.court, judge: d.judge, url: d.doc_url, status: "1" });
        edge(did, cid, "decision_of", { date });
      }
    }
    if (t.tool === "find_fop" && o.found) {
      const name = String(t.input.name || "").replace(/ФОП/gi, "").replace(/\s+/g, " ").trim();
      const pid = node("person:" + lc(name), "person", name);
      want(pid, 1);
      // found by the name (_identity.js): one distinct name among the records -> probable, several -> namesakes
      const n = countCandidates(o.records || [], () => 1, (r) => identityKey(r.name)).get(1) || 0;
      for (const r of o.records || []) {
        const fid = node("fop:" + r.record, "registry", "ФОП " + r.name, { stan: r.stan, text: `${r.stan || ""}; ${r.registration || ""}`, src: "ЄДР (ФОП)" });
        const L = nameLink("fop_record", n, { detail: "збіг за імʼям", src: "ЄДР" }, "ФОП");
        edge(pid, fid, L.rel, L.attrs);
      }
    }
    if (t.tool === "search_decisions") {
      for (const c of o.cases || []) if (c.cause_num) node("case:" + lc(c.cause_num), "case", "Справа " + c.cause_num, { ref: c.cause_num });
      for (const d of o.decisions || []) {
        const date = dt(d.adjudication_date), did = node("decision:" + d.doc_id, "decision", `${d.judgment_form || "Рішення"} ${date}`, { doc_id: d.doc_id, form: d.judgment_form, date, court: d.court, judge: d.judge, url: d.doc_url });
        if (d.cause_num) edge(did, node("case:" + lc(d.cause_num), "case", "Справа " + d.cause_num, { ref: d.cause_num }), "decision_of", { date });
      }
    }
    if (t.tool === "search_entities") {
      for (const e of o.edr || []) node("registry:" + e.edrpou, "registry", `ЄДР ${e.edrpou} · ${e.short_name || ""}`, { edrpou: e.edrpou, stan: e.stan });
      for (const x of o.orgs || []) node("org:" + lc(x.name), "org", x.name, x.edrpou ? { edrpou: x.edrpou } : {});
      for (const x of o.persons || []) node("person:" + lc(x.name_norm), "person", x.name_norm, { position: x.position });
    }
  }
  if (!nodes.size) return null;
  // keep the focus and its direct links when trimming
  const keep = [...nodes.values()].sort((a, b) => (b.id === focus) - (a.id === focus)).slice(0, 160), ids = new Set(keep.map((n) => n.id));
  return { nodes: keep, edges: edges.filter((e) => ids.has(e.source) && ids.has(e.target)).slice(0, 400), focus };
}

// ---- direct answers without a model: case numbers and "ФОП <name>" are recognised by pattern (free, instant) ----
const dt = (d) => { if (!d) return "?"; const x = d instanceof Date ? d : new Date(d); return isNaN(x) ? String(d).slice(0, 10) : new Date(x.getTime() - x.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const plural = (n, one, few, many) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many; };

async function direct(text) {
  const c = text.match(CAUSE_ANY);
  // only a bare case number (plus a word or two) is answered without the model; «справа N і як повʼязаний X» needs connect
  const rest = c ? text.replace(c[0], " ").replace(/[^\p{L}\s]/gu, " ").split(/\s+/).filter((w) => w.length > 2 && !/^(справ\p{L}*|дело|дела|розкажи|расскажи|про|що|что|номер|покажи)$/iu.test(w)) : [];
  if (c && rest.length === 0) {
    const trace = [];
    const out = await call("get_case", { cause_num: c[0] }, trace);
    if (out.error) return null;
    if (!out.found) return { answer: `Справи ${c[0]} у базі ще немає. Поставив її в чергу: рішення з'являться за 5–20 хвилин, тоді запитайте ще раз.`, trace };
    const s = out.summary;
    const lines = [`**Справа ${c[0]}** — ${s.n} ${plural(s.n, "рішення", "рішення", "рішень")}, ${dt(s.d0)} … ${dt(s.d1)}.`,
      `Суди: ${s.court_list || "?"}.${s.judges ? ` Судді: ${s.judges}.` : ""}${s.category ? ` Категорія: ${s.category}.` : ""}`,
      `Форми: ${out.forms.map((f) => `${f.f || "?"} ×${f.n}`).join(", ")}.`, "", "Останні рішення:",
      ...out.timeline.slice(0, 15).map((t) => `- ${dt(t.d)} · ${t.f || "рішення"} · ${t.court || ""}${t.judge ? " · " + t.judge : ""}${t.doc_url ? ` · [текст](${t.doc_url})` : ""}`)];
    // the case's people, posts and press around it
    await call("entity_dossier", { id: "case:" + c[0].toLowerCase() }, trace);
    const d = trace.at(-1).summary;
    if (d.media?.length) lines.push("", "У ЗМІ повʼязують: " + d.media.slice(0, 5).map((m) => `${m.name_norm} (${m.role})`).join(", ") + ".");
    if (d.posts?.found) lines.push(`Постів зі згадкою номера: ${d.posts.found}${d.posts.more ? "+" : ""}.`);
    return { answer: lines.join("\n"), trace };
  }
  const f = text.match(FOP_RE);
  if (f) {
    const name = f[1].trim(), trace = [];
    const out = await call("find_fop", { name }, trace);
    if (out.error) return { answer: out.error, trace: [] };
    if (!out.found) return { answer: `ФОП «${name}» ще не шукали. Поставив пошук у чергу: за 5–10 хвилин запитайте ще раз.`, trace };
    const lines = [`**ЄДР, ФОП «${name}»** — ${out.found >= 20 ? "20+ записів (показано 20)" : out.found + " " + plural(out.found, "запис", "записи", "записів")}. Збіг за імʼям: це можуть бути різні люди.`, "",
      ...out.records.map((r) => `- ${r.name} · ${r.stan || "стан ?"} · реєстрація: ${r.registration || "?"}${r.terminated ? " · припинення: " + r.terminated : ""}`)];
    return { answer: lines.join("\n"), trace };
  }
  return null;
}

// ---- before the model: resolve the question and gather what it obviously needs, in parallel ----
async function prestep(text, trace) {
  const r = await call("resolve", { q: text }, trace);
  if (r.error) return r;
  const k = r.kinds || [], best = (r.candidates || []).find((c) => c.id === r.best);
  const jobs = [];
  if (best && best.type !== "place" && best.tier <= 2) jobs.push(["entity_dossier", { id: best.id }]);
  const day = r.date || r.today;
  if (k.includes("strikes") || (k.includes("place") && r.date && !best)) jobs.push(["strikes", { region: r.region?.id || "", date_from: day, date_to: day }]);
  else if (best?.type === "place" && !k.includes("rhetoric")) jobs.push(["entity_dossier", { id: best.id }]);
  if (k.includes("rhetoric") && (k.includes("strikes") || r.date)) jobs.push(["rhetoric_before", { date: day, days: 1, ...(r.region ? { region: r.region.id } : {}) }]);
  await Promise.all(jobs.map(([n, a]) => call(n, a, trace)));
  return r;
}
function context(trace) {
  return "Сервер уже виконав (не повторюй ці виклики, використай результати):\n" +
    trace.map((t) => `${t.tool}(${JSON.stringify(t.input)}) → ${JSON.stringify(t.summary).slice(0, t.tool === "resolve" ? 3000 : 9000)}`).join("\n\n");
}

// ---- a plain answer from the gathered data when no model is configured or the model failed ----
function brief(trace) {
  const L = [];
  for (const t of trace) {
    const o = t.summary || {};
    if (o.error) continue;
    if (t.tool === "resolve" && !trace.some((x) => ["entity_dossier", "strikes", "rhetoric_before"].includes(x.tool)) && o.candidates?.length)
      L.push("Знайдено в базі: " + o.candidates.slice(0, 6).map((c) => `${c.label}${c.sub ? " (" + c.sub + ")" : ""}`).join("; ") + ".");
    if (t.tool === "entity_dossier") {
      L.push(`**${o.node?.label || o.label}**${o.node?.position ? " — " + o.node.position : ""}${o.source ? ` (${o.source.country || ""} ${o.source.type || ""})` : ""}`);
      if (o.activity) L.push(`Постів у базі: ${o.activity.n}, за тиждень ${o.activity.week}.`);
      if (o.court?.found) L.push(`Рішень у справі: ${o.court.found} (${o.court.summary.d0}…${o.court.summary.d1}), ${o.court.summary.court_list || ""}.`);
      if (o.media?.length) L.push("У ЗМІ повʼязують: " + o.media.slice(0, 5).map((m) => `${m.name_norm} (${m.role})`).join(", ") + ".");
      if (o.roles?.length) L.push("Ролі в постах: " + o.roles.map((r) => `${r.role} ×${r.n} (${r.d0}…${r.d1})`).join(", ") + ".");
      if (o.cases?.length) L.push("Справи: " + [...new Set(o.cases.map((c) => c.nickname || c.ref).filter(Boolean))].join(", ") + ".");
      if (o.media_cases?.length) L.push("За ЗМІ повʼязаний зі справами: " + o.media_cases.map((m) => m.cause_num).join(", ") + ".");
      if (o.decisions?.length) L.push(`Рішень судів зі згадкою: ${o.decisions.length}` + (o.decisions[0] ? ` (остання ${o.decisions[0].d}, справа ${o.decisions[0].cause_num})` : "") + ".");
      if (o.posts) L.push(`Постів зі згадкою: ${o.posts.found}${o.posts.more ? "+" : ""}.`, ...(o.posts.items || []).slice(0, 6).map((p) => `- ${p.date} · ${p.src || p.source}: ${clip(p.snippet, 160)}${p.url ? ` [пост](${p.url})` : ""}`));
      if (o.web?.length) L.push("В інтернеті: " + o.web.slice(0, 3).map((w) => `[${clip(w.title, 60)}](${w.url})`).join(", ") + ".");
      if (o.neighbourhood) L.push(`У графі: ${o.neighbourhood.total} повʼязаних обʼєктів.`);
    }
    if (t.tool === "strikes") {
      L.push(`**Удари ${o.date_from}${o.date_to !== o.date_from ? "…" + o.date_to : ""}${o.region ? ", " + o.region.name : ""}**`);
      for (const w of o.waves || []) L.push(`- Україна, ${w.date} (${w.kind || "хвиля"}): ракет ${w.launched_missiles ?? "?"}, дронів ${w.launched_drones ?? "?"}, збито ${w.shot_down_total ?? "?"}${w.source_url ? ` [джерело](${w.source_url})` : ""}`);
      for (const d of o.kharkiv_daily || []) L.push(`- Харківщина, ${d.date}: ракет ${d.missiles ?? 0}, дронів ${d.drones ?? 0}, КАБ ${d.guided_bombs ?? 0}${d.city_attacked ? ", Харків атаковано" : ""}${d.url ? ` [зведення](${d.url})` : ""}`);
      for (const d of (Array.isArray(o.oblast_days) ? o.oblast_days : []).slice(0, 8)) L.push(`- ${d.region_id === o.region?.id ? o.region.name : d.region_id} (зведення/події), ${d.date}: ракет ${d.missiles ?? 0}, дронів ${d.drones ?? 0}, КАБ ${d.kab ?? 0}`);
      for (const a of Array.isArray(o.alerts) ? o.alerts : []) L.push(`- тривоги ${a.date} (${a.source}): ${a.n}, ${a.minutes} хв`);
      if (!(o.waves?.length || o.kharkiv_daily?.length || o.oblast_days?.length || o.events?.length)) L.push("За ці дні в базі ударів не знайдено (джерела могли ще не зібрати день).");
    }
    if (t.tool === "rhetoric_before") {
      L.push(`**Російські канали ${o.window.from}…${o.window.to_exclusive} (не включно)**: постів ${o.totals.posts}, зі словами погроз ${o.totals.threat_words}${o.region ? `, згадують ${o.region.name} ${o.totals.mentions_region}` : ""}; у середньому за 14 днів до того — ${o.baseline_14d?.posts_per_day ?? "?"} постів і ${o.baseline_14d?.threat_words_per_day ?? "?"} з погрозами на день.`);
      L.push(...(o.posts || []).filter((p) => p.mentions_region || p.threat_level >= 2).slice(0, 6).map((p) => `- ${p.date} ${p.time} · ${p.src || p.source}${p.threat_level != null ? " · погроза " + p.threat_level : ""}: ${clip(p.snippet, 180)}${p.url ? ` [пост](${p.url})` : ""}`));
    }
  }
  return L.length ? L.join("\n") : null;
}

// ---- model providers: Claude when ANTHROPIC_API_KEY is set, otherwise Gemini (free tier) ----
async function claudeLoop(messages, key, trace, t0) {
  for (let step = 0; step < 6; step++) {
    // stop before the function limit (vercel.json maxDuration 60 s): answer from what the tools found so far
    if (Date.now() - t0 > 40000) return null;
    const t = Date.now();
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST", signal: AbortSignal.timeout(Math.max(5000, 47000 - (t - t0))),
      headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01",
        // keys not scoped to a workspace must name one
        ...(process.env.ANTHROPIC_WORKSPACE_ID ? { "anthropic-workspace-id": process.env.ANTHROPIC_WORKSPACE_ID } : {}) },
      body: JSON.stringify({ model: "claude-haiku-5-5", max_tokens: 4000, system: SYSTEM(kyivToday()), tools: TOOLS, messages, output_config: { effort: "low" } }),
    });
    const data = await r.json();
    console.log("claude step", step, Date.now() - t, "ms", data.usage?.output_tokens, data.stop_reason);
    if (data.error) throw new Error(data.error.message || "api error");
    const toolUses = (data.content || []).filter((b) => b.type === "tool_use");
    if (!toolUses.length || data.stop_reason !== "tool_use") return (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
    messages = [...messages, { role: "assistant", content: data.content }];
    // tools of one step run in parallel; results go back in one user message, in call order
    const outs = await Promise.all(toolUses.map((tu) => call(tu.name, tu.input, trace)));
    messages = [...messages, { role: "user", content: toolUses.map((tu, i) => ({ type: "tool_result", tool_use_id: tu.id, content: JSON.stringify(outs[i]).slice(0, 12000) })) }];
  }
  return null;
}

function geminiSchema(s) {
  if (Array.isArray(s)) return s.map(geminiSchema);
  if (!s || typeof s !== "object") return s;
  const o = {};
  for (const [k, v] of Object.entries(s)) {
    if (k === "default") continue;
    o[k] = k === "type" ? String(v).toUpperCase() : k === "properties" ? Object.fromEntries(Object.entries(v).map(([pk, pv]) => [pk, geminiSchema(pv)])) : geminiSchema(v);
  }
  return o;
}

async function geminiLoop(messages, key, trace, t0) {
  const model = process.env.GEMINI_MODEL || "gemini-3.5-flash-lite";
  const tools = [{ functionDeclarations: TOOLS.map((t) => ({ name: t.name, description: t.description, parameters: geminiSchema(t.input_schema) })) }];
  let contents = messages.filter((m) => m.content && (typeof m.content === "string" ? m.content.trim() : true))
    .map((m) => ({ role: m.role === "assistant" ? "model" : "user", parts: typeof m.content === "string" ? [{ text: m.content }] : m.content.map((b) => ({ text: b.text })) }));
  for (let step = 0; step < 6; step++) {
    if (Date.now() - t0 > 40000) return null;
    const req = () => fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
      method: "POST", signal: AbortSignal.timeout(Math.max(5000, 47000 - (Date.now() - t0))),
      headers: { "content-type": "application/json", "x-goog-api-key": key },
      body: JSON.stringify({ systemInstruction: { parts: [{ text: SYSTEM(kyivToday()) }] }, contents, tools, generationConfig: { temperature: 0.2, maxOutputTokens: 3000 } }),
    });
    const t = Date.now();
    let r = await req(), data = await r.json();
    console.log("gemini step", step, Date.now() - t, "ms", r.status);
    // free tier: per-minute limit. Wait once if Google says it is short (RetryInfo.retryDelay) and the function has time left.
    if (r.status === 429) {
      const wait = Number(((data.error?.details || []).find((d) => d.retryDelay)?.retryDelay || "").replace("s", "")) || 6;
      if (wait <= 12 && Date.now() - t0 < 15000) { await new Promise((ok) => setTimeout(ok, wait * 1000)); r = await req(); data = await r.json(); }
    }
    if (r.status === 429) return null;
    if (data.error) throw new Error(data.error.message || "gemini error");
    const parts = data.candidates?.[0]?.content?.parts || [];
    const calls = parts.filter((p) => p.functionCall);
    if (!calls.length) return parts.filter((p) => p.text).map((p) => p.text).join("\n") || null;
    contents = [...contents, { role: "model", parts }];
    // same as claudeLoop: tools of one step in parallel, each capped at 12 s
    const outs = await Promise.all(calls.map(({ functionCall: fc }) => call(fc.name, fc.args, trace)));
    contents = [...contents, { role: "user", parts: calls.map(({ functionCall: fc }, i) => {
      const str = JSON.stringify(outs[i]);
      return { functionResponse: { name: fc.name, response: str.length > 12000 ? { truncated: str.slice(0, 12000) } : { result: outs[i] } } };
    }) }];
  }
  return null;
}

const wire = (trace) => trace.map(({ _graph, ...t }) => t);

export default async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "POST" });
  const t0 = Date.now();
  let body = "";
  for await (const c of req) body += c;
  let messages;
  try { messages = JSON.parse(body).messages.filter((m) => typeof m.content === "string").slice(-12); } catch { return json(res, 400, { error: "bad body" }); }
  const last = [...messages].reverse().find((m) => m.role === "user")?.content || "";
  if (!last.trim()) return json(res, 400, { error: "empty question" });
  let trace = [];
  try {
    // short lookups go straight to the base; longer questions go to the model when one is configured (it has the same tools)
    const hasModel = !!(process.env.ANTHROPIC_API_KEY || process.env.GEMINI_API_KEY);
    const d = !hasModel || last.length <= 80 ? await direct(last) : null;
    if (d) return json(res, 200, { answer: d.answer, trace: wire(d.trace), graph: toGraph(d.trace), mode: "direct" });
    await withTimeout(prestep(last, trace), 16000).catch((e) => console.log("prestep", e.message));
    trace = [...trace];  // calls still running after the cap may push later; the snapshot is what the model sees
    const pre = trace.length ? context(trace) : "";
    const msgs = messages.map((m, i) => (i === messages.length - 1 && m.role === "user" && pre ? { role: "user", content: [{ type: "text", text: m.content }, { type: "text", text: pre }] } : m));
    let answer = null, mode = "direct";
    if (process.env.ANTHROPIC_API_KEY) {
      mode = "claude";
      answer = await claudeLoop(msgs, process.env.ANTHROPIC_API_KEY, trace, t0).catch((e) => { console.log("claude", e.message); return null; });
    }
    if (!answer && process.env.GEMINI_API_KEY && Date.now() - t0 < 30000) {
      mode = "gemini";
      answer = await geminiLoop(msgs, process.env.GEMINI_API_KEY, trace, t0).catch((e) => { console.log("gemini", e.message); return null; });
    }
    if (!answer) {
      const b = brief(trace);
      if (!b && !hasModel) return json(res, 503, { error: "Чат ще не підключено: на Vercel немає GEMINI_API_KEY (безкоштовно) чи ANTHROPIC_API_KEY. Номер справи або «ФОП Прізвище Імʼя» працюють і без ключа.", trace: wire(trace) });
      answer = (hasModel ? "Модель не відповіла вчасно; ось що знайдено в базі:\n\n" : "") + (b || "Нічого не знайшов за цим запитом. Уточніть імʼя, назву чи дату.");
      mode = hasModel ? mode + "+fallback" : "direct";
    }
    return json(res, 200, { answer, trace: wire(trace), graph: toGraph(trace), mode, ms: Date.now() - t0 });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e), trace: wire(trace) });
  }
}

function summarize(o) {
  if (!o || typeof o !== "object") return o;
  const s = {};
  for (const [k, v] of Object.entries(o)) s[k] = Array.isArray(v) ? v.slice(0, 25) : v;
  return s;
}
