-- 020: topics for statements and labels (полонені, обміни, перемир'я, ТЦК, закони, корупція) and the Ukrainian mood view.
-- Idempotent. Apply: gh workflow run migrate -f files="db/migrations/020_topics_mood.sql"

-- Statement topic: set by the extractor (prompt st-v2); older rows get it from the text by topic_of() below.
ALTER TABLE statements ADD COLUMN IF NOT EXISTS topic text;
CREATE INDEX IF NOT EXISTS statements_topic_idx ON statements (topic) WHERE topic IS NOT NULL;

-- Exchanges carry a head count (people); ceasefire events use event_type 'ceasefire' (no new columns needed otherwise).
ALTER TABLE extracted_events ADD COLUMN IF NOT EXISTS people integer;

-- Same keyword classes as src/topics.py (keep in sync). First match wins, in this order: an exchange post also mentions
-- prisoners, so 'exchange' is tested before 'pow'.
CREATE OR REPLACE FUNCTION topic_of(t text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN t ~* '(обмін\w* полонен|обмен\w* (военно)?пленн|обмін\w* тіл|обмен\w* тел|prisoner (swap|exchange)|\mswap\M)' THEN 'exchange'
    WHEN t ~* '(полонен|\mполоні\M|військовополон|военнопл|\mпленн|\mв плену|\mплен(ные|ных|ным|ными)\M|prisoners? of war|\mPOWs?\M)' THEN 'pow'
    WHEN t ~* '(перемир|прекращени\w* огня|припинен\w* вогню|режим\w* тиш|ceasefire|truce|мораторі|моратори|енергетичн\w* перемир)' THEN 'ceasefire'
    WHEN t ~* '(\mТЦК|військкомат|военкомат|мобілізац|мобилизац|повістк|\mповестк(а|и|у|ой|ами|ам)?\M(?!\s+дн)|бусифік|ухилянт|Резерв\+|відстрочк|отсрочк|демобіліз|демобилиз)' THEN 'tcc'
    WHEN t ~* '(\mНАБУ\M|\mСАП\M|\mВАКС\M|\mНАЗК\M|корупц|коррупц|хабар(?!овс)|взятк|розкрада|розтрат)' THEN 'corruption'
    WHEN t ~* '(законопроєкт|законопроект|Верховн\w* Рад|\mВРУ\M|ухвалил\w* закон|підписав закон|ветував|постанов\w* (КМУ|Кабмін|уряду)|указ президента|закон про)' THEN 'law'
  END $$;

-- Backfill statements that predate the extractor's own topic (re-runnable; never overwrites the model's value).
UPDATE statements SET topic = topic_of(coalesce(quote, '') || ' ' || coalesce(predicted_event, '')) WHERE topic IS NULL;

-- statements_x gains topic (column appended at the end so CREATE OR REPLACE keeps the existing column order).
CREATE OR REPLACE VIEW statements_x AS
SELECT s.id, s.message_id, s.ts_published, s.actor_side, s.actor_name, s.actor_id, s.stype, s.quote, s.predicted_event, s.target_region,
       s.horizon_days, s.resolvable, s.status, s.resolved_at, s.evidence_ids, s.resolver_note, m.source_id, m.url,
       coalesce(a.grp, g.id, 'other') AS grp,
       coalesce(a.country, g.country, CASE src.country WHEN 'UA' THEN 'UA' WHEN 'RU' THEN 'RU' WHEN 'US' THEN 'US' ELSE 'other' END) AS country,
       s.topic
FROM statements s
JOIN messages m ON m.id = s.message_id
LEFT JOIN sources src ON src.id = m.source_id
LEFT JOIN actors a ON a.id = s.actor_id
LEFT JOIN LATERAL (SELECT id, country FROM actor_groups WHERE s.actor_side = ANY(sides) ORDER BY ord LIMIT 1) g ON true;

-- Mood: one row per (labeled post, topic) from the labeling prompt v3 field "topics":[{topic, stance -2..2, object}].
-- Only valid topics and stances pass; the API aggregates by week / source country / source type.
CREATE OR REPLACE VIEW topic_mood AS
SELECT l.message_id, m.ts_published, s.country, s.type AS source_type, m.source_id,
       x->>'topic' AS topic, CASE WHEN (x->>'stance') ~ '^-?[0-2]$' THEN (x->>'stance')::int END AS stance, nullif(left(trim(x->>'object'), 80), '') AS object
FROM message_labels l
JOIN messages m ON m.id = l.message_id
JOIN sources s ON s.id = m.source_id
CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(l.raw->'topics') = 'array' THEN l.raw->'topics' ELSE '[]'::jsonb END) x
WHERE x->>'topic' IN ('tcc', 'law', 'corruption', 'pow', 'exchange', 'ceasefire')
  AND (x->>'stance') ~ '^-?[0-2]$';

-- Self-confirmation does not count (src/resolve_statements.py independent()): a 'true' / 'partial' verdict whose evidence is
-- only the speaker's own channel goes back to 'unresolvable' with a marker, and --retry re-checks it with the new rules.
-- On 2026-10-22 this was 40 of 64 true/partial verdicts (RU 17 of 21, UA 15 of 34): what pushed RU / UA trust to 95% / 93%.
UPDATE statements s SET status = 'unresolvable', resolver_note = left('лише джерело мовця: ' || coalesce(s.resolver_note, ''), 300)
FROM messages m
WHERE m.id = s.message_id AND s.status IN ('true', 'partial')
  AND NOT EXISTS (SELECT 1 FROM unnest(coalesce(s.evidence_ids, '{}')) e
                  WHERE e LIKE 'gt:%' OR EXISTS (SELECT 1 FROM messages em WHERE em.id = e AND em.source_id <> m.source_id));
