import { sql, json, classify, query } from "./_db.js";

// POST /api/chat {messages:[{role,content}]} -> answer + structured results. Claude Haiku with tools over the database.
const SYSTEM = `Ти — асистент дослідницької бази про українські антикорупційні справи, судові рішення, заяви сторін і публічну риторику.
Відповідай українською (або мовою користувача), стисло, з конкретикою: номери справ, суди, судді, дати, назви компаній, ЄДРПОУ.
Користуйся інструментами, щоб шукати в базі; не вигадуй даних. Якщо в базі потрібного зрізу ще немає (наприклад, рішення іншого року чи категорії),
скажи це прямо і постав у чергу індексацію або ланцюжок по реєстрах через enqueue, пояснивши, що результат з'явиться за 10–30 хвилин.
Категорії справ у реєстрі — українські назви (наприклад, "земельні" справи: шукай за фрагментом "земел"). Рік із фрази "за 25-й рік" = 2025.
У відповіді перелічуй знайдене списком; для справ давай номер, суд, кількість рішень, діапазон дат.
Номер справи (формат 991/1234/21) → get_case: хронологія рішень із посиланнями. Якщо рішень у базі немає, get_case сам ставить справу в чергу.
"Знайди ФОП …" → find_fop з повним імʼям. Це лише запис ЄДР ФОП (стан, дата реєстрації, припинення); збіг за імʼям може бути однофамільцем — кажи це.
Статистика судів за всіма видами проваджень (цивільні, зокрема сімейні; кримінальні; адміністративні; господарські; адмінправопорушення) → court_stats.
Сімейні справи: category_re "сімейн|шлюб|аліме|батьківськ|усиновл". Кримінальні: justice_kind "Кримінальне"; цивільно-правові: "Цивільне".
Це агрегати реєстру (кількість рішень і справ, частка з вироком чи рішенням, медіана тривалості), не прогноз для конкретної особи.
Пам'ятай: у кримінальних "справах" багато ухвал слідчих суддів (обшуки, арешти), тому частка з вироком низька; кажи це.
Не зводь ФОП із судами, боргами чи іншими реєстрами в одну відповідь про людину і не роби висновків про вину конкретних осіб.`;

const TOOLS = [
  { name: "search_decisions", description: "Пошук судових рішень у базі (індекс ЄДРСР). Повертає групування по справах або список рішень.",
    input_schema: { type: "object", properties: {
      year: { type: "integer" }, category: { type: "string", description: "фрагмент назви категорії, напр. 'земел'" },
      court: { type: "string", description: "фрагмент назви суду" }, judge: { type: "string" }, form: { type: "string", description: "Вирок|Ухвала|Рішення|Постанова" },
      cause_num: { type: "string" }, justice_kind: { type: "string", description: "Кримінальне|Цивільне|Адміністративне|Господарське" },
      group_by_case: { type: "boolean", default: true }, limit: { type: "integer", default: 30 } } } },
  { name: "list_categories", description: "Які категорії справ і суди є в індексі (з кількістю), щоб зрозуміти покриття.", input_schema: { type: "object", properties: { year: { type: "integer" }, like: { type: "string" } } } },
  { name: "search_entities", description: "Люди, компанії (з ЄДРПОУ), справи й активи з антикорупційних постів.", input_schema: { type: "object", properties: { q: { type: "string" } }, required: ["q"] } },
  { name: "search_corpus", description: "Повнотекстовий пошук по постах, новинах, заявах і текстах рішень у базі.", input_schema: { type: "object", properties: { q: { type: "string" }, since: { type: "string" }, until: { type: "string" }, limit: { type: "integer", default: 15 } }, required: ["q"] } },
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
      const rows = await query(`SELECT cause_num, max(court) court, count(*)::int n, min(adjudication_date) d0, max(adjudication_date) d1, max(category) category, string_agg(DISTINCT judgment_form, ', ') forms FROM court_decisions ${w} GROUP BY cause_num ORDER BY n DESC LIMIT ${lim}`, params);
      const tot = await query(`SELECT count(*)::int n, count(DISTINCT cause_num)::int cases FROM court_decisions ${w}`, params);
      return { total_decisions: tot[0].n, total_cases: tot[0].cases, cases: rows };
    }
    return { decisions: await query(`SELECT doc_id, cause_num, court, judgment_form, category, adjudication_date, judge, doc_url FROM court_decisions ${w} ORDER BY adjudication_date DESC LIMIT ${lim}`, params) };
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
  if (name === "search_corpus") {
    const pat = "%" + a.q + "%";
    const lim = Math.min(40, a.limit || 15);
    const rows = await sql`SELECT m.id, m.source_id, m.ts_published, m.url, left(regexp_replace(m.text,'\s+',' ','g'), 300) snippet FROM messages m
      WHERE m.text ILIKE ${pat} AND (${a.since || null}::timestamptz IS NULL OR m.ts_published >= ${a.since || null}) AND (${a.until || null}::timestamptz IS NULL OR m.ts_published <= ${a.until || null})
      ORDER BY m.ts_published DESC LIMIT ${lim}`;
    const tot = await sql`SELECT count(*)::int n FROM messages WHERE text ILIKE ${pat}`;
    return { total: tot[0].n, items: rows };
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
    const key = num.toLowerCase();
    const [sum] = await sql`SELECT count(*)::int n, min(adjudication_date) d0, max(adjudication_date) d1, count(DISTINCT court)::int courts,
                                   string_agg(DISTINCT court, ' · ') court_list, string_agg(DISTINCT judge, ' · ') judges, count(text)::int texts,
                                   max(category) category, max(justice_kind) justice_kind FROM court_decisions WHERE (lower(cause_num) = ${key} OR lower(cause_num) LIKE ${key + '-%'})`;
    if (!sum.n) return { found: 0, ...(await enqueue(num, "case")) };
    const forms = await sql`SELECT judgment_form f, count(*)::int n FROM court_decisions WHERE (lower(cause_num) = ${key} OR lower(cause_num) LIKE ${key + '-%'}) GROUP BY 1 ORDER BY 2 DESC`;
    const timeline = await sql`SELECT doc_id, adjudication_date d, judgment_form f, court, judge, doc_url FROM court_decisions WHERE (lower(cause_num) = ${key} OR lower(cause_num) LIKE ${key + '-%'}) ORDER BY adjudication_date DESC NULLS LAST LIMIT 40`;
    return { found: sum.n, summary: sum, forms, timeline };
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
  if (name === "enqueue") return enqueue((a.query || "").trim(), a.kind);
  return { error: "unknown tool" };
}

// ---- graph payload: what the tools found, as nodes/edges with the same ids as /api/graph, so the page can merge and focus ----
const lc = (x) => String(x || "").replace(/\s+/g, " ").trim().toLowerCase();
function toGraph(trace) {
  const nodes = new Map(), edges = [];
  let focus = null;
  const node = (id, type, label, attrs = {}) => { if (!nodes.has(id)) nodes.set(id, { id, type, label, ...attrs }); focus ??= id; return id; };
  const edge = (source, target, rel, attrs = {}) => edges.push({ source, target, rel, msgs: [], ...attrs });
  for (const t of trace) {
    const o = t.summary || {};
    if (t.tool === "get_case" && o.found) {
      const num = t.input.cause_num, cid = node("case:" + lc(num), "case", "Справа " + num, { ref: num });
      for (const d of o.timeline || []) {
        if (!d.doc_id) continue;
        const date = dt(d.d), did = node("decision:" + d.doc_id, "decision", `${d.f || "Рішення"} ${date}`, { doc_id: d.doc_id, form: d.f, date, court: d.court, judge: d.judge, url: d.doc_url, status: "1" });
        edge(did, cid, "decision_of", { date });
      }
    }
    if (t.tool === "find_fop" && o.found) {
      const name = String(t.input.name || "").replace(/ФОП/gi, "").replace(/\s+/g, " ").trim();
      const pid = node("person:" + lc(name), "person", name);
      for (const r of o.records || []) {
        const fid = node("fop:" + r.record, "registry", "ФОП " + r.name, { stan: r.stan, text: `${r.stan || ""}; ${r.registration || ""}`, src: "ЄДР (ФОП)" });
        edge(pid, fid, "fop_record", { detail: "збіг за імʼям (можливий однофамілець)", src: "ЄДР" });
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
  return nodes.size ? { nodes: [...nodes.values()].slice(0, 60), edges: edges.slice(0, 120), focus } : null;
}

// ---- direct answers without a model: case numbers and "ФОП <name>" are recognised by pattern (free, instant) ----
const CAUSE_ANY = /\d{1,4}\/\d{1,6}\/\d{2}(?:-[\p{L}\d/]+)?/u;
const CASE_FULL = /^\d{1,4}\/\d{1,6}\/\d{2}(?:-[\p{L}\d/]+)?$/u;
const FOP_RE = /(?:^|\s)фоп\s+([\p{L}'’ʼ-]+(?:\s+[\p{L}'’ʼ-]+){1,2})/iu;
const dt = (d) => { if (!d) return "?"; const x = d instanceof Date ? d : new Date(d); return isNaN(x) ? String(d).slice(0, 10) : new Date(x.getTime() - x.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const plural = (n, one, few, many) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many; };

async function direct(text) {
  const c = text.match(CAUSE_ANY);
  if (c) {
    const out = await runTool("get_case", { cause_num: c[0] });
    if (out.error) return null;
    if (!out.found) return { answer: `Справи ${c[0]} у базі ще немає. Поставив її в чергу: рішення з'являться за 5–20 хвилин, тоді запитайте ще раз.`, trace: [{ tool: "get_case", input: { cause_num: c[0] }, summary: out }] };
    const s = out.summary;
    const lines = [`**Справа ${c[0]}** — ${s.n} ${plural(s.n, "рішення", "рішення", "рішень")}, ${dt(s.d0)} … ${dt(s.d1)}.`,
      `Суди: ${s.court_list || "?"}.${s.judges ? ` Судді: ${s.judges}.` : ""}${s.category ? ` Категорія: ${s.category}.` : ""}`,
      `Форми: ${out.forms.map((f) => `${f.f || "?"} ×${f.n}`).join(", ")}.`, "", "Останні рішення:",
      ...out.timeline.slice(0, 15).map((t) => `- ${dt(t.d)} · ${t.f || "рішення"} · ${t.court || ""}${t.judge ? " · " + t.judge : ""}${t.doc_url ? ` · [текст](${t.doc_url})` : ""}`)];
    return { answer: lines.join("\n"), trace: [{ tool: "get_case", input: { cause_num: c[0] }, summary: summarize(out) }] };
  }
  const f = text.match(FOP_RE);
  if (f) {
    const name = f[1].trim();
    const out = await runTool("find_fop", { name });
    if (out.error) return { answer: out.error, trace: [] };
    if (!out.found) return { answer: `ФОП «${name}» ще не шукали. Поставив пошук у чергу: за 5–10 хвилин запитайте ще раз.`, trace: [{ tool: "find_fop", input: { name }, summary: out }] };
    const lines = [`**ЄДР, ФОП «${name}»** — ${out.found >= 20 ? "20+ записів (показано 20)" : out.found + " " + plural(out.found, "запис", "записи", "записів")}. Збіг за імʼям: це можуть бути різні люди.`, "",
      ...out.records.map((r) => `- ${r.name} · ${r.stan || "стан ?"} · реєстрація: ${r.registration || "?"}${r.terminated ? " · припинення: " + r.terminated : ""}`)];
    return { answer: lines.join("\n"), trace: [{ tool: "find_fop", input: { name }, summary: summarize(out) }] };
  }
  return null;
}

const withTimeout = (p, ms) => Promise.race([p, new Promise((_, no) => setTimeout(() => no(new Error(`timeout ${ms / 1000} s`)), ms))]);

// ---- model providers: Claude when ANTHROPIC_API_KEY is set, otherwise Gemini (free tier) ----
async function claudeLoop(messages, key, trace) {
  const t0 = Date.now();
  for (let step = 0; step < 6; step++) {
    // stop before the function limit (vercel.json maxDuration 60 s): answer from what the tools found so far
    if (Date.now() - t0 > 40000) return "Не встиг завершити пошук за відведений час; показую знайдене на графі. Уточніть запит.";
    const t = Date.now();
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST", signal: AbortSignal.timeout(Math.max(5000, 45000 - (t - t0))),
      headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01" },
      body: JSON.stringify({ model: "claude-haiku-5-5", max_tokens: 4000, system: SYSTEM, tools: TOOLS, messages, output_config: { effort: "low" } }),
    });
    const data = await r.json();
    console.log("claude step", step, Date.now() - t, "ms", data.usage?.output_tokens, data.stop_reason);
    if (data.error) throw new Error(data.error.message || "api error");
    const toolUses = (data.content || []).filter((b) => b.type === "tool_use");
    if (!toolUses.length || data.stop_reason !== "tool_use") return (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
    messages = [...messages, { role: "assistant", content: data.content }];
    // tools of one step run in parallel; results go back in one user message, in call order
    const outs = await Promise.all(toolUses.map(async (tu) => {
      const t1 = Date.now();
      const out = await withTimeout(runTool(tu.name, tu.input || {}), 12000).catch((e) => ({ error: String(e.message || e) }));
      return { out, ms: Date.now() - t1 };
    }));
    const results = toolUses.map((tu, i) => {
      const { out, ms } = outs[i];
      console.log("tool", tu.name, ms, "ms");
      trace.push({ tool: tu.name, input: tu.input, ms, summary: summarize(out) });
      return { type: "tool_result", tool_use_id: tu.id, content: JSON.stringify(out).slice(0, 12000) };
    });
    messages = [...messages, { role: "user", content: results }];
  }
  return "Забагато кроків; уточніть запит.";
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

async function geminiLoop(messages, key, trace) {
  const t0 = Date.now();
  const model = process.env.GEMINI_MODEL || "gemini-3.5-flash-lite";
  const tools = [{ functionDeclarations: TOOLS.map((t) => ({ name: t.name, description: t.description, parameters: geminiSchema(t.input_schema) })) }];
  let contents = messages.filter((m) => typeof m.content === "string" && m.content.trim())
    .map((m) => ({ role: m.role === "assistant" ? "model" : "user", parts: [{ text: m.content }] }));
  for (let step = 0; step < 6; step++) {
    if (Date.now() - t0 > 40000) return "Не встиг завершити пошук за відведений час; показую знайдене на графі. Уточніть запит.";
    const call = () => fetch(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
      method: "POST", signal: AbortSignal.timeout(Math.max(5000, 45000 - (Date.now() - t0))),
      headers: { "content-type": "application/json", "x-goog-api-key": key },
      body: JSON.stringify({ systemInstruction: { parts: [{ text: SYSTEM }] }, contents, tools, generationConfig: { temperature: 0.2, maxOutputTokens: 3000 } }),
    });
    const t = Date.now();
    let r = await call(), data = await r.json();
    console.log("gemini step", step, Date.now() - t, "ms", r.status);
    // free tier: per-minute limit. Wait once if Google says it is short (RetryInfo.retryDelay) and the function has time left.
    if (r.status === 429) {
      const wait = Number(((data.error?.details || []).find((d) => d.retryDelay)?.retryDelay || "").replace("s", "")) || 6;
      if (wait <= 12 && Date.now() - t0 < 15000) { await new Promise((ok) => setTimeout(ok, wait * 1000)); r = await call(); data = await r.json(); }
    }
    if (r.status === 429) return "Ліміт безкоштовного тарифу Gemini вичерпано. Спробуйте за хвилину. Номери справ і «ФОП Прізвище Імʼя» працюють і без моделі.";
    if (data.error) throw new Error(data.error.message || "gemini error");
    const parts = data.candidates?.[0]?.content?.parts || [];
    const calls = parts.filter((p) => p.functionCall);
    if (!calls.length) return parts.filter((p) => p.text).map((p) => p.text).join("\n") || "Модель не дала відповіді; переформулюйте запит.";
    contents = [...contents, { role: "model", parts }];
    // same as claudeLoop: tools of one step in parallel, each capped at 12 s
    const outs = await Promise.all(calls.map(async ({ functionCall: fc }) => {
      const t1 = Date.now();
      const out = await withTimeout(runTool(fc.name, fc.args || {}), 12000).catch((e) => ({ error: String(e.message || e) }));
      return { out, ms: Date.now() - t1 };
    }));
    const responses = calls.map(({ functionCall: fc }, i) => {
      const { out, ms } = outs[i];
      console.log("tool", fc.name, ms, "ms");
      trace.push({ tool: fc.name, input: fc.args, ms, summary: summarize(out) });
      const str = JSON.stringify(out);
      return { functionResponse: { name: fc.name, response: str.length > 12000 ? { truncated: str.slice(0, 12000) } : { result: out } } };
    });
    contents = [...contents, { role: "user", parts: responses }];
  }
  return "Забагато кроків; уточніть запит.";
}

export default async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "POST" });
  let body = "";
  for await (const c of req) body += c;
  let messages;
  try { messages = JSON.parse(body).messages.slice(-12); } catch { return json(res, 400, { error: "bad body" }); }
  const last = [...messages].reverse().find((m) => m.role === "user" && typeof m.content === "string")?.content || "";
  const trace = [];
  try {
    // short lookups go straight to the base; longer questions go to the model when one is configured (it has the same tools)
    const hasModel = !!(process.env.ANTHROPIC_API_KEY || process.env.GEMINI_API_KEY);
    const d = !hasModel || last.length <= 80 ? await direct(last) : null;
    if (d) return json(res, 200, { ...d, graph: toGraph(d.trace), mode: "direct" });
    if (process.env.ANTHROPIC_API_KEY) { const answer = await claudeLoop(messages, process.env.ANTHROPIC_API_KEY, trace); return json(res, 200, { answer, trace, graph: toGraph(trace), mode: "claude" }); }
    if (process.env.GEMINI_API_KEY) { const answer = await geminiLoop(messages, process.env.GEMINI_API_KEY, trace); return json(res, 200, { answer, trace, graph: toGraph(trace), mode: "gemini" }); }
    return json(res, 503, { error: "Чат ще не підключено: на Vercel немає GEMINI_API_KEY (безкоштовно) чи ANTHROPIC_API_KEY. Номер справи або «ФОП Прізвище Імʼя» працюють і без ключа." });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e), trace });
  }
}

function summarize(o) {
  if (!o || typeof o !== "object") return o;
  const s = {};
  for (const [k, v] of Object.entries(o)) s[k] = Array.isArray(v) ? v.slice(0, 20) : v;
  return s;
}
