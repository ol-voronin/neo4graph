-- Server-side search (web/api/search.js): ranked exact -> prefix -> trigram over short name columns.
-- No index on messages.text: post search is a capped, recent-first scan.
-- search_names is refreshed by src/drain_queue.py and src/extract_entities.py (load_to_db.refresh_search_names).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- tables the web/worker code creates on first use; declared here so the view below can always be built
CREATE TABLE IF NOT EXISTS registry_hits (id bigserial PRIMARY KEY, query text NOT NULL, qkind text, source text NOT NULL, ref text NOT NULL,
  title text, snippet text, url text, payload jsonb, created_at timestamptz DEFAULT now(), UNIQUE (query, source, ref));
CREATE TABLE IF NOT EXISTS analyst_links (id bigserial PRIMARY KEY, src_kind text NOT NULL, src_name text NOT NULL, dst_kind text NOT NULL, dst_name text NOT NULL,
  relation text NOT NULL, note text, created_at timestamptz DEFAULT now(), UNIQUE (src_kind, src_name, dst_kind, dst_name, relation));
CREATE TABLE IF NOT EXISTS media_links (id bigserial PRIMARY KEY, cause_num text, name_norm text, name text, role text,
  position text, quote text, url text, confidence real, model text, created_at timestamptz DEFAULT now(), UNIQUE (cause_num, name_norm, url));

-- One row per graph name: key = lower(name with collapsed spaces), i.e. the part after "person:" / "org:" in /api/graph ids.
-- Cases keep the raw label; the id is derived in JS (caseKey) exactly as /api/graph does.
DROP MATERIALIZED VIEW IF EXISTS search_names;
CREATE MATERIALIZED VIEW search_names AS
WITH raw(kind, label) AS (
  SELECT 'person', name_norm FROM ee_persons
  UNION ALL SELECT 'person', linked_person FROM ee_assets WHERE linked_person IS NOT NULL
  UNION ALL SELECT 'person', name_norm FROM media_links
  UNION ALL SELECT 'org', name FROM ee_orgs
  UNION ALL SELECT 'case', coalesce(nickname, ref) FROM ee_cases WHERE coalesce(nickname, ref) IS NOT NULL
  -- ЄДР founders / signers / beneficiaries as /api/graph adds them (cooperatives with > 60 founders are skipped there too)
  UNION ALL SELECT CASE WHEN split_part(f, ';', 2) ~ '(^|\D)\d{8}(\D|$)' THEN 'org' ELSE 'person' END, split_part(f, ';', 1)
    FROM edr_orgs, jsonb_array_elements_text(founders || signers || beneficiaries) f WHERE jsonb_array_length(founders) <= 60
  -- searched names the registry chain attached results to
  UNION ALL SELECT CASE WHEN query ~* '^(тов|пат|прат|ат|дп|кп)\M' THEN 'org' ELSE 'person' END, query
    FROM registry_hits WHERE qkind IS DISTINCT FROM 'case' AND query !~ '^\d'
  UNION ALL SELECT src_kind, src_name FROM analyst_links WHERE src_kind IN ('person', 'org')
  UNION ALL SELECT dst_kind, dst_name FROM analyst_links WHERE dst_kind IN ('person', 'org')
)
SELECT kind, lower(n) AS key, min(n) AS label, count(*)::int AS n
FROM (SELECT kind, btrim(regexp_replace(label, '\s+', ' ', 'g')) AS n FROM raw) x
WHERE n <> '' AND length(n) <= 300
GROUP BY 1, 2;
CREATE UNIQUE INDEX search_names_pk ON search_names (kind, key);   -- needed for REFRESH ... CONCURRENTLY
CREATE INDEX search_names_trgm ON search_names USING gin (key gin_trgm_ops);

-- companies: ЄДР names; FOP / company cards from the registry worker (titles only)
CREATE INDEX IF NOT EXISTS edr_orgs_name_trgm ON edr_orgs USING gin (lower(coalesce(short_name, '') || ' ' || name) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS registry_hits_title_trgm ON registry_hits USING gin (lower(title) gin_trgm_ops) WHERE source IN ('edr_fop', 'yc_fop', 'yc_org');
CREATE INDEX IF NOT EXISTS registry_hits_edrpou_idx ON registry_hits ((payload->>'edrpou')) WHERE payload ? 'edrpou';
-- places: extracted event regions (short strings)
CREATE INDEX IF NOT EXISTS extracted_events_region_trgm ON extracted_events USING gin (lower(region) gin_trgm_ops) WHERE region IS NOT NULL;
-- case-number prefix ("991/12…"): LIKE 'x%' can only use a btree with pattern ops under a non-C collation
CREATE INDEX IF NOT EXISTS court_decisions_cause_pattern_idx ON court_decisions (cause_num text_pattern_ops);
