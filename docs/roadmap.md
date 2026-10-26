# Roadmap (8 weeks), from the product feedback of 2026-10-26

Source: an outside review of the product (positioning against Опендатабот / YouControl, monetisation, roadmap, tariffs).
Owner's take (2026-10-26): projects first, search by РНОКПП is a goal.

## What the review got right
Links between cases rather than a counterparty card; «реєстр» vs «ЗМІ» kept apart; fact-check and chat over the graph;
the rhetoric / strikes layer as a niche of its own; open data and no paid subscription.

## What the review did not know (already done)
- The UA worker runs on the Kyiv VPS as a systemd service with a heartbeat (deploy/vps.md) — its «week 1».
- Shortest chains between two entities with evidence per step exist (web/api/_connect.js) — its «week 7» is a button.
- «Check a claim» exists as link check + statement fact-check (web/api/linkcheck.js, factcheck.js); needs a paste-a-quote entry.
- Source labels partly exist on edges («за даними ЗМІ», registry, probable); they need to be visible in the UI and in exports.
- The researcher teaches the search / graph with ✓/✗ (web/api/feedback.js) — the seed for «AI finds links» inside a project.

## Disagreement
Monitoring as the first paid feature is premature: without accounts and a user's own set of entities there is nothing to
subscribe to. Projects give both, and alerts + export attach to a project. So projects go first.

## Projects (the owner's favourite idea)
- A project = name, members, nodes and edges picked from the shared graph, own nodes (client, witness, document), notes,
  query history, ✓/✗ marks, evidence per link.
- Pieces: each member works in their own branch; merge = union by identifiers (ЄДРПОУ, case number, ЄРДР) plus the
  namesake check (web/api/_identity.js) with a review queue of conflicts («та сама особа / інша»).
- «Find links»: for every pair of project entities, graph paths + pairwise registry / decision-text search, then the model
  writes the list of found links with quotes and source URLs, labelled as model output.
- Three users: a lawyer (client's case, parties, courts, assets), a legal due-diligence team, a newsroom investigation.

## Plan
1. Weeks 1–2 — accounts (magic link by e-mail, Google sign-in), project core: create, add nodes from search / graph, notes,
   share link; export of a project to CSV / JSON with a source URL on every link.
2. Weeks 3–4 — team: members and roles, branches, merge with a conflict queue; «find links» on top of _connect.js;
   source labels (registry / media / model) visible in the graph and in exports.
3. Weeks 5–6 — monitoring of a project's entities (new decision, declaration, ЄДР change) to Telegram and e-mail;
   PDF / PNG export with source links.
4. Weeks 7–8 — tariffs Free / Pro / Team, request limits, priority worker queue for Team, public API beta, payments.

## Risks
One VPS serves the whole UA-registry queue: teams will need a second worker. Data freshness is below the commercial
services and will not be closed in 8 weeks: print the data date on every card. The rhetoric layer is research, not a
forecast (AUC ≈ 0.7, CI touches zero).

## Login and workspace model (to decide)
Analogy: Google Drive for the container model (projects as files in folders, sharing by link with viewer / commenter /
editor roles, organisations), Figma for the editing model (one live canvas per project, presence, comments pinned to
nodes, branches with merge). Sign-in: magic link + Google; the site key becomes an organisation invite.
