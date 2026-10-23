-- 029: hourly "current estimate" per oblast (src/nowcast.py live): the v3 models refitted with the rhetoric window ending
-- at the current hour, for the next Kyiv day. NOT the prospective test: predictions stay frozen once a day; these rows are
-- a running view of the same model on the freshest posts, kept for the intraday curve. Idempotent.
CREATE TABLE IF NOT EXISTS live_estimates (
  model text NOT NULL, region_id text NOT NULL, target_date date NOT NULL, computed_at timestamptz NOT NULL DEFAULT now(),
  cutoff_hour smallint NOT NULL, p real NOT NULL, n_train int, features jsonb,
  PRIMARY KEY (model, region_id, computed_at));
CREATE INDEX IF NOT EXISTS live_estimates_recent_idx ON live_estimates (target_date, model, computed_at DESC);
