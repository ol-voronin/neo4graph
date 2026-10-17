-- Postgres schema (Neon, PG17). Mirrors what is deployed in project `rhetoric-strikes`.
-- All timestamps in UTC (timestamptz).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE sources (
  id text PRIMARY KEY, name text NOT NULL, country text, lang text,
  type text CHECK (type IN ('official','state_media','milblogger','independent','analyst','osint','agency')),
  platform text, reliability real, noise_level real, created_at timestamptz DEFAULT now()
);

CREATE TABLE narratives (
  id bigserial PRIMARY KEY, label text NOT NULL, first_seen_ts timestamptz, last_seen_ts timestamptz
);

CREATE TABLE messages (
  id text PRIMARY KEY,
  source_id text REFERENCES sources(id),
  ts_published timestamptz NOT NULL,
  ts_collected timestamptz DEFAULT now(),
  lang text, text text, url text, hash text,  -- hash is NOT unique: identical repeated posts in monitoring feeds are real signal
  event_kind text CHECK (event_kind IN ('rhetoric','operational','anticorruption','decision','other')),
  threat_level smallint CHECK (threat_level BETWEEN 0 AND 3),
  novelty real, target_mentioned boolean, timeframe_mentioned boolean,
  is_scheduled_context boolean, tone_amplification boolean, llm_confidence real,
  narrative_id bigint REFERENCES narratives(id),
  prompt_version text, labeled_at timestamptz,
  embedding vector(1024)
);
CREATE INDEX messages_ts_idx ON messages (ts_published);
CREATE INDEX messages_hash_idx ON messages (hash);
CREATE INDEX messages_source_ts_idx ON messages (source_id, ts_published);
CREATE INDEX messages_narrative_idx ON messages (narrative_id);

CREATE TABLE entities (
  id bigserial PRIMARY KEY, name text NOT NULL,
  type text CHECK (type IN ('person','region','weapon','org','city')),
  UNIQUE (name, type)
);
CREATE TABLE message_entities (
  message_id text REFERENCES messages(id) ON DELETE CASCADE,
  entity_id bigint REFERENCES entities(id),
  PRIMARY KEY (message_id, entity_id)
);

CREATE TABLE events (
  id bigserial PRIMARY KEY, ts timestamptz NOT NULL,
  type text CHECK (type IN ('decision','summit','holiday','strike_minor','alert','other')),
  region text, severity smallint, scheduled boolean DEFAULT false, description text, source_url text
);
CREATE INDEX events_ts_idx ON events (ts);

CREATE TABLE regions (
  id text PRIMARY KEY, name text NOT NULL, level text CHECK (level IN ('oblast','hromada','city'))
);

-- Waves are versioned: revised figures create a new version, old ones are kept.
CREATE TABLE waves (
  id text NOT NULL, version int NOT NULL DEFAULT 1, date date NOT NULL,
  ts_start timestamptz, ts_end timestamptz,
  launched_missiles int, launched_drones int, intercepted_missiles int, intercepted_drones int,
  is_massive boolean,
  precision text CHECK (precision IN ('oblast','hromada','city')),
  source text, confidence real, revised_at timestamptz DEFAULT now(),
  PRIMARY KEY (id, version)
);
CREATE INDEX waves_date_idx ON waves (date);

-- Per-city/region counts: the "massive" definition is applied per single city.
CREATE TABLE wave_regions (
  wave_id text NOT NULL, wave_version int NOT NULL,
  region_id text REFERENCES regions(id),
  missiles int, drones int, source text, confidence real,
  PRIMARY KEY (wave_id, wave_version, region_id),
  FOREIGN KEY (wave_id, wave_version) REFERENCES waves(id, version) ON DELETE CASCADE
);

CREATE TABLE alerts (
  id bigserial PRIMARY KEY, region_id text REFERENCES regions(id),
  ts_start timestamptz NOT NULL, ts_end timestamptz, alert_type text, source text
);
CREATE INDEX alerts_region_ts_idx ON alerts (region_id, ts_start);

-- Stores case STATUS, never a verdict.
CREATE TABLE anticorruption_cases (
  id bigserial PRIMARY KEY,
  side text CHECK (side IN ('UA','RU','EU','other')),
  agency text,
  stage text CHECK (stage IN ('suspicion','detention','preventive_measure','indictment','verdict')),
  ts_stage timestamptz, official_level text, sector text,
  amount numeric, amount_currency text,
  proximity text CHECK (proximity IN ('none','ministry','defense_procurement','presidential_circle')),
  source_url text, status_note text
);
CREATE INDEX cases_ts_idx ON anticorruption_cases (ts_stage);

CREATE TABLE case_narratives (
  case_id bigint REFERENCES anticorruption_cases(id) ON DELETE CASCADE,
  narrative_id bigint REFERENCES narratives(id) ON DELETE CASCADE,
  lag_h real, PRIMARY KEY (case_id, narrative_id)
);
CREATE TABLE case_entities (
  case_id bigint REFERENCES anticorruption_cases(id) ON DELETE CASCADE,
  entity_id bigint REFERENCES entities(id), PRIMARY KEY (case_id, entity_id)
);

CREATE TABLE config (
  key text PRIMARY KEY, value jsonb NOT NULL, set_at timestamptz DEFAULT now()
);

-- "Massive strike" threshold, set by the user (single city: 7-10 missiles OR 20 drones).
-- missiles_default is used for the main analysis; 7..10 is for sensitivity analysis.
INSERT INTO config(key, value) VALUES ('massive_threshold',
  '{"scope":"single_city","missiles_min":7,"missiles_max":10,"drones_min":20,"missiles_default":7,"status":"set by user; 7-10 range used for sensitivity analysis"}');

CREATE VIEW wave_massive AS
SELECT w.id AS wave_id, w.version, w.date,
  COALESCE(bool_or(
    wr.missiles >= (SELECT (value->>'missiles_default')::int FROM config WHERE key='massive_threshold')
    OR wr.drones >= (SELECT (value->>'drones_min')::int FROM config WHERE key='massive_threshold')
  ), false) AS is_massive_city,
  max(wr.missiles) AS max_missiles_one_city,
  max(wr.drones) AS max_drones_one_city
FROM waves w
LEFT JOIN wave_regions wr ON wr.wave_id = w.id AND wr.wave_version = w.version
GROUP BY w.id, w.version, w.date;
