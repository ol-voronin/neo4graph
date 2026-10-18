-- Anti-corruption layer: raw LLM entity extractions per message (docs/entity_extraction.md).
-- Graph-ready but kept flat in Postgres until entity resolution is validated on a test set of cases.
CREATE TABLE IF NOT EXISTS entity_extractions (
    message_id      text PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    is_case_related boolean NOT NULL,
    payload         jsonb NOT NULL,
    pii_dropped     boolean NOT NULL DEFAULT false,
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS entity_extractions_persons_idx ON entity_extractions USING gin ((payload->'persons'));
CREATE INDEX IF NOT EXISTS entity_extractions_case_idx ON entity_extractions (is_case_related);

-- Flat views for quick querying and for the later Neo4j export.
CREATE OR REPLACE VIEW ee_persons AS
SELECT e.message_id, m.source_id, m.ts_published,
       p->>'name_norm' AS name_norm, p->>'name' AS name_as_written, p->>'role' AS role,
       p->>'position' AS position, p->>'org' AS org
FROM entity_extractions e JOIN messages m ON m.id = e.message_id,
     jsonb_array_elements(e.payload->'persons') p
WHERE p->>'role' <> 'private_skipped' AND p->>'name_norm' IS NOT NULL;

CREATE OR REPLACE VIEW ee_cases AS
SELECT e.message_id, m.source_id, m.ts_published,
       c->>'ref' AS ref, c->>'nickname' AS nickname, c->>'agency' AS agency, c->>'stage' AS stage, c->>'summary' AS summary
FROM entity_extractions e JOIN messages m ON m.id = e.message_id,
     jsonb_array_elements(e.payload->'cases') c;

CREATE OR REPLACE VIEW ee_assets AS
SELECT e.message_id, m.source_id, m.ts_published,
       a->>'type' AS type, a->>'description' AS description, a->>'plate_or_vin' AS plate_or_vin, a->>'linked_person' AS linked_person
FROM entity_extractions e JOIN messages m ON m.id = e.message_id,
     jsonb_array_elements(e.payload->'assets') a;

CREATE OR REPLACE VIEW ee_orgs AS
SELECT e.message_id, m.source_id, m.ts_published,
       o->>'name' AS name, o->>'edrpou' AS edrpou, o->>'kind' AS kind, o->>'role' AS role
FROM entity_extractions e JOIN messages m ON m.id = e.message_id,
     jsonb_array_elements(e.payload->'orgs') o;
