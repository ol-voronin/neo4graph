-- Fact-checks of arbitrary links (web/api/linkcheck.js): one row per URL, the model's answer with its sources; reused for 24 h.
CREATE TABLE IF NOT EXISTS link_checks (
  url text PRIMARY KEY, info jsonb, result jsonb, model text, checked_at timestamptz NOT NULL DEFAULT now());
