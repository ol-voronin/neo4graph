-- Aggregates of the ЄДРСР open-data dumps (all justice kinds), written by src/lake_edrsr.py; the full metadata lives in
-- the lake (edrsr_<year>.parquet). Rows for a year are replaced on every rebuild of that year.
CREATE TABLE IF NOT EXISTS edrsr_stats (
  year int NOT NULL, justice_kind text, category text, region text, form text, n int NOT NULL);
CREATE INDEX IF NOT EXISTS edrsr_stats_idx ON edrsr_stats (year, justice_kind);
CREATE TABLE IF NOT EXISTS edrsr_case_stats (
  year int NOT NULL, justice_kind text, category text, region text,
  cases int NOT NULL, with_verdict int, with_decision int, with_court_order int, median_days real, p75_days real);
CREATE INDEX IF NOT EXISTS edrsr_case_stats_idx ON edrsr_case_stats (year, justice_kind);
