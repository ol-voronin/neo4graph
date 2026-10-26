-- 037: open data of the map.ukrainealarm.com page (src/collect_ukrainealarm.py, VPS timers). Idempotent.
-- ua_targets_day / ua_targets_region: targets the map tracked per Kyiv day (shahed, jet-shahed, missile, airbomb = КАБ,
--   banderol, unidentified), nationally by type and hour, and per region. The source keeps only ~7 days, so the
--   VPS fetches today and yesterday every evening and morning; a day is overwritten until it stops changing.
-- ua_alarms: alarms per district / community with level and reason ("Ракетна загроза (червоний рівень)"), from the
--   map's per-oblast "last alerts" list; ts_end is NULL while the alarm is on.
-- region_id is our oblast slug (src/oblasts.py), NULL for a city community the map lists as its own region.
CREATE TABLE IF NOT EXISTS ua_targets_day (
  d date PRIMARY KEY, total int NOT NULL, types jsonb NOT NULL, hours jsonb NOT NULL, tracking jsonb,
  generated_at timestamptz, fetched_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS ua_targets_region (
  d date NOT NULL, ua_region_id text NOT NULL, region_id text, name text NOT NULL, total int NOT NULL, tracks int,
  PRIMARY KEY (d, ua_region_id));
CREATE INDEX IF NOT EXISTS ua_targets_region_slug_idx ON ua_targets_region (region_id, d);

CREATE TABLE IF NOT EXISTS ua_alarms (
  id bigint PRIMARY KEY, ua_region_id text NOT NULL, ua_oblast_id text, region_id text, region_name text,
  ts_start timestamptz NOT NULL, ts_end timestamptz, alert_type text, level text, reason text,
  fetched_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS ua_alarms_region_idx ON ua_alarms (region_id, ts_start);
