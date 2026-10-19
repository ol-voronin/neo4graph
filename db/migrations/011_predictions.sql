-- Prospective forecasts (src/nowcast.py). A row is written before the outcome window opens and its p is never updated;
-- `outcome` is filled later by `nowcast.py evaluate`. Primary key = one forecast per model and target day.
CREATE TABLE IF NOT EXISTS predictions (
  model text NOT NULL, target_date date NOT NULL, issued_at timestamptz NOT NULL DEFAULT now(),
  p real NOT NULL, n_train int, features jsonb, features_hash text, spec_hash text,
  outcome smallint, scored_at timestamptz,
  PRIMARY KEY (model, target_date));
