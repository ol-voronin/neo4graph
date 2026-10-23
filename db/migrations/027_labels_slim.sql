-- 027: message_labels keeps only what is read in the database; the raw LLM JSON (entities, case block, narrative label
-- and copies of the messages columns, ~60 MB) moves to Parquet in the release "labels-archive" (src/labels_archive.py),
-- once a row is older than 14 days. topics (prompt v3) feeds the mood view, so it gets its own column. Idempotent.

ALTER TABLE message_labels ADD COLUMN IF NOT EXISTS topics jsonb;
ALTER TABLE message_labels ALTER COLUMN raw DROP NOT NULL;
ALTER TABLE message_labels ADD COLUMN IF NOT EXISTS archived_at timestamptz;  -- raw moved to labels-archive at this time
UPDATE message_labels SET topics = raw->'topics' WHERE topics IS NULL AND jsonb_typeof(raw->'topics') = 'array';

CREATE OR REPLACE VIEW topic_mood AS
SELECT l.message_id, m.ts_published, s.country, s.type AS source_type, m.source_id,
       x->>'topic' AS topic, CASE WHEN (x->>'stance') ~ '^-?[0-2]$' THEN (x->>'stance')::int END AS stance, nullif(left(trim(x->>'object'), 80), '') AS object
FROM message_labels l
JOIN messages m ON m.id = l.message_id
JOIN sources s ON s.id = m.source_id
-- raw->'topics' covers rows written by a labeler that predates this migration (raw is archived only after 14 days)
CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(coalesce(l.topics, l.raw->'topics')) = 'array' THEN coalesce(l.topics, l.raw->'topics') ELSE '[]'::jsonb END) x
WHERE x->>'topic' IN ('tcc', 'law', 'corruption', 'pow', 'exchange', 'ceasefire')
  AND (x->>'stance') ~ '^-?[0-2]$';
