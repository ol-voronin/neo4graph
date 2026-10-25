-- What the researcher teaches the graph and the search (web/api/feedback.js):
--   link_feedback: a verdict on one drawn edge (source id, target id, relation): 'yes' = confirmed, 'no' = wrong (hidden);
--   search_picks:  which result was opened for a query; search ranks results picked before for the same query first.
CREATE TABLE IF NOT EXISTS link_feedback (
  a text NOT NULL, b text NOT NULL, rel text NOT NULL,
  verdict text NOT NULL CHECK (verdict IN ('yes', 'no')),
  note text, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (a, b, rel));
CREATE TABLE IF NOT EXISTS search_picks (
  q text NOT NULL, id text NOT NULL, label text, n int NOT NULL DEFAULT 1, last_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (q, id));
