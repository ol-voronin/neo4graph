-- ЄДРСР aggregates were 102 MB, mostly the same ~1.8k category names (avg 65 chars) repeated in every row.
-- Names move to edrsr_dict; the tables keep smallint codes. Views under the old names keep every reader
-- (api/chat.js court_stats) unchanged. src/lake_edrsr.py writes the *_c tables.
BEGIN;

CREATE TABLE IF NOT EXISTS edrsr_dict (
  id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  field "char" NOT NULL,          -- k justice_kind, c category, r region, f form
  value text NOT NULL,
  UNIQUE (field, value));

INSERT INTO edrsr_dict (field, value)
SELECT DISTINCT f, v FROM (
  SELECT 'k'::"char" f, justice_kind v FROM edrsr_stats UNION SELECT 'c', category FROM edrsr_stats
  UNION SELECT 'r', region FROM edrsr_stats UNION SELECT 'f', form FROM edrsr_stats
  UNION SELECT 'k', justice_kind FROM edrsr_case_stats UNION SELECT 'c', category FROM edrsr_case_stats
  UNION SELECT 'r', region FROM edrsr_case_stats) x
WHERE v IS NOT NULL
ON CONFLICT DO NOTHING;

CREATE TABLE edrsr_stats_c (
  year smallint NOT NULL, jk smallint, cat smallint, reg smallint, frm smallint, n int NOT NULL);
INSERT INTO edrsr_stats_c
SELECT s.year, k.id, c.id, r.id, f.id, s.n FROM edrsr_stats s
  LEFT JOIN edrsr_dict k ON k.field = 'k' AND k.value = s.justice_kind
  LEFT JOIN edrsr_dict c ON c.field = 'c' AND c.value = s.category
  LEFT JOIN edrsr_dict r ON r.field = 'r' AND r.value = s.region
  LEFT JOIN edrsr_dict f ON f.field = 'f' AND f.value = s.form;

CREATE TABLE edrsr_case_stats_c (
  year smallint NOT NULL, jk smallint, cat smallint, reg smallint,
  cases int NOT NULL, with_verdict int, with_decision int, with_court_order int, median_days real, p75_days real);
INSERT INTO edrsr_case_stats_c
SELECT s.year, k.id, c.id, r.id, s.cases, s.with_verdict, s.with_decision, s.with_court_order, s.median_days, s.p75_days FROM edrsr_case_stats s
  LEFT JOIN edrsr_dict k ON k.field = 'k' AND k.value = s.justice_kind
  LEFT JOIN edrsr_dict c ON c.field = 'c' AND c.value = s.category
  LEFT JOIN edrsr_dict r ON r.field = 'r' AND r.value = s.region;

DROP TABLE edrsr_stats;
DROP TABLE edrsr_case_stats;
CREATE INDEX edrsr_stats_c_idx ON edrsr_stats_c (year, jk);
CREATE INDEX edrsr_case_stats_c_idx ON edrsr_case_stats_c (year, jk);

CREATE VIEW edrsr_stats AS
SELECT s.year::int AS year, k.value justice_kind, c.value category, r.value region, f.value form, s.n FROM edrsr_stats_c s
  LEFT JOIN edrsr_dict k ON k.id = s.jk LEFT JOIN edrsr_dict c ON c.id = s.cat
  LEFT JOIN edrsr_dict r ON r.id = s.reg LEFT JOIN edrsr_dict f ON f.id = s.frm;
CREATE VIEW edrsr_case_stats AS
SELECT s.year::int AS year, k.value justice_kind, c.value category, r.value region,
       s.cases, s.with_verdict, s.with_decision, s.with_court_order, s.median_days, s.p75_days FROM edrsr_case_stats_c s
  LEFT JOIN edrsr_dict k ON k.id = s.jk LEFT JOIN edrsr_dict c ON c.id = s.cat LEFT JOIN edrsr_dict r ON r.id = s.reg;

COMMIT;
