// The chat sees what the graph shows (index.html sends `graph` with every message): a command about the current view
// («сортуй за датою», «лише 2025 рік», «прибери пости», «глибина 3») comes back as actions the page applies; a question
// («хто з них засновник?») goes on to the usual chat with the graph state attached, so «вони», «ця справа» resolve to it.
const PLAN = `Ти керуєш переглядом графа звʼязків на сайті. Тобі дають стан графа (центр, фільтри, доступні значення фільтрів,
видимі обʼєкти) і повідомлення користувача. Визнач, чи це КОМАНДА для поточного перегляду (сортувати, фільтрувати, прибрати/
показати типи, змінити глибину, перейти до обʼєкта з видимих, показати списком/графом, скинути фільтри), чи ПИТАННЯ до бази.
Дії (лише ці):
 {"type":"sort","by":"date|name|links|type","dir":"asc|desc"}   — показує списком, відсортованим
 {"type":"filter","facet":"<ключ фасету>","value":"<точне значення з facets>"}   — залишити лише це значення (кілька — кілька дій)
 {"type":"remove_filter","facet":"<ключ>"}   {"type":"clear_filters"}
 {"type":"text","value":"<слово>"}   — лише обʼєкти, де є це слово
 {"type":"depth","value":1|2|3}   {"type":"view","value":"list|graph"}
 {"type":"focus","id":"<id з видимих обʼєктів>"}
Фасети: type (тип вузла), year, region, court, patr (по батькові), form (форма рішення), kind (судочинство), org, stan, rel (тип звʼязку), prov (походження).
Значення фільтра бери ТІЛЬКИ з facets (для type — ключі типів: person, org, case, decision, post, registry, …).
Відповідай ЛИШЕ JSON: {"kind":"command|question","actions":[...],"reply":"коротко, що зроблено (для команди)"}`;

export function graphText(g) {
  if (!g || !g.center) return "";
  const f = (g.filters || []).map((x) => `${x.label || x.facet}: ${x.value}`).join("; ");
  const by = Object.entries(g.counts || {}).map(([t, n]) => `${t} ${n}`).join(", ");
  const nodes = (g.nodes || []).slice(0, 40).map((n) => `${n.label} [${n.type}${n.date ? ", " + n.date : ""}]`).join("; ");
  return `Зараз у графі користувача: центр «${g.center.label}» (${g.center.type}, ${g.center.id}), глибина ${g.depth}` + (f ? `, фільтри: ${f}` : "") +
    `. Видно: ${by}. Обʼєкти: ${nodes}. Якщо питання про «них», «цю справу», «цих людей» — це про цей граф.`;
}

export async function planGraph(text, g, key) {
  if (!g || !g.center || !key) return null;
  const facets = Object.fromEntries(Object.entries(g.facets || {}).map(([k, v]) => [k, (v.values || []).map(([val, n]) => `${val} (${n})`)]));
  const state = JSON.stringify({ center: g.center, depth: g.depth, view: g.view, filters: g.filters, facets, counts: g.counts,
    nodes: (g.nodes || []).slice(0, 60).map((n) => ({ id: n.id, label: n.label, type: n.type, date: n.date })) });
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST", signal: AbortSignal.timeout(15000),
    headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01",
      ...(process.env.ANTHROPIC_WORKSPACE_ID ? { "anthropic-workspace-id": process.env.ANTHROPIC_WORKSPACE_ID } : {}) },
    body: JSON.stringify({ model: "claude-haiku-5-5", max_tokens: 800, system: PLAN, output_config: { effort: "low" },
      messages: [{ role: "user", content: `Стан графа: ${state}\n\nПовідомлення: ${text}` }] }),
  });
  const d = await r.json();
  if (d.error) throw new Error(d.error.message || "model error");
  const t = (d.content || []).filter((b) => b.type === "text").map((b) => b.text).join("");
  const out = JSON.parse((t.match(/\{[\s\S]*\}/) || ["{}"])[0]);
  const ok = new Set(["sort", "filter", "remove_filter", "clear_filters", "text", "depth", "view", "focus"]);
  out.actions = (out.actions || []).filter((a) => a && ok.has(a.type));
  return out;
}
