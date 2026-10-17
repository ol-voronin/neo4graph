-- Applied to Neon project rhetoric-strikes on 2026-10-17.

-- waves: nationwide summary columns from kpszsu posts (see src/parse_kpszsu.py)
ALTER TABLE waves
  ADD COLUMN IF NOT EXISTS kind text,                      -- night | day
  ADD COLUMN IF NOT EXISTS launched_other int,             -- S-8000 "Banderol" etc.
  ADD COLUMN IF NOT EXISTS shot_down_total int,
  ADD COLUMN IF NOT EXISTS hit_locations int,
  ADD COLUMN IF NOT EXISTS debris_locations int,
  ADD COLUMN IF NOT EXISTS missiles_unspecified boolean,   -- true: missile types named without numbers (count is a lower bound)
  ADD COLUMN IF NOT EXISTS source_url text;

-- alerts: dataset provenance
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS naive boolean, ADD COLUMN IF NOT EXISTS level text;
CREATE UNIQUE INDEX IF NOT EXISTS alerts_uniq ON alerts (region_id, ts_start, source);

-- messages.hash is not unique: identical repeated posts in monitoring feeds are signal
ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_hash_key;
CREATE INDEX IF NOT EXISTS messages_hash_idx ON messages (hash);

-- fact-checking layer (docs/factcheck.md)
CREATE TABLE IF NOT EXISTS claims (
  id bigserial PRIMARY KEY,
  message_id text REFERENCES messages(id) ON DELETE CASCADE,
  claim_text text NOT NULL,
  claim_type text CHECK (claim_type IN ('strike_claim','casualty_claim','damage_claim','interception_claim','statement_of_intent','political_claim','economic_claim','other')),
  actor text, place text, claim_date date, quantity numeric, quantity_unit text,
  checkable boolean DEFAULT true, extracted_by text, extraction_version text, extracted_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS claims_msg_idx ON claims (message_id);
CREATE INDEX IF NOT EXISTS claims_date_idx ON claims (claim_date);

CREATE TABLE IF NOT EXISTS claim_checks (
  id bigserial PRIMARY KEY,
  claim_id bigint REFERENCES claims(id) ON DELETE CASCADE,
  verdict text NOT NULL CHECK (verdict IN ('supported','partially_supported','contradicted','no_evidence_found','unverifiable','outdated')),
  confidence real, method text, evidence jsonb, evidence_urls text[], note text,
  check_version text, checked_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS claim_checks_claim_idx ON claim_checks (claim_id);

CREATE TABLE IF NOT EXISTS source_accuracy (
  source_id text REFERENCES sources(id), period_start date, period_end date,
  claims_checked int, supported int, contradicted int, no_evidence int,
  PRIMARY KEY (source_id, period_start, period_end)
);

-- scheduled-events calendar (fixed-date holidays/anniversaries, 2022-2026)
INSERT INTO events(ts, type, region, severity, scheduled, description)
SELECT make_timestamptz(y, t.m, t.d, 0, 0, 0, 'UTC'), 'holiday', t.scope, 0, true, t.descr
FROM generate_series(2022,2026) y
CROSS JOIN (VALUES (1,1,'both','New Year'),(2,23,'RU','Defender of the Fatherland Day (Russia)'),
 (2,24,'both','Anniversary of the full-scale invasion'),(5,9,'RU','Victory Day (Russia)'),(6,12,'RU','Russia Day'),
 (6,28,'UA','Constitution Day (Ukraine)'),(8,24,'UA','Independence Day (Ukraine)'),(10,1,'UA','Defenders Day (Ukraine)'),
 (10,7,'RU','Putin birthday'),(11,4,'RU','Unity Day (Russia)'),(12,6,'UA','Armed Forces Day (Ukraine)'),
 (12,31,'both','New Year eve')) AS t(m,d,scope,descr);

-- 2026-10-17: regional channel discovery
ALTER TABLE sources ADD COLUMN IF NOT EXISTS region text, ADD COLUMN IF NOT EXISTS tier text;
-- 28 regional/national monitoring channels inserted (see channels.json, tier A/B); Kharkiv sources tagged region='Kharkivska'.
