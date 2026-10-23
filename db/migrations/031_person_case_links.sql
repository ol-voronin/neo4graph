-- 031: people named in court decision texts, resolved against a register (src/person_cases.py). First role: lawyers
-- (ЄРАУ). certainty: 'можливо' = exactly one candidate in the register (a link, shown as a probable identity, never as
-- proof); 'неоднозначно' = several candidates (no link); 'немає в реєстрі' = none. Rebuilt per role. Idempotent.
CREATE TABLE IF NOT EXISTS person_case_links (
  cause_num text, doc_id text NOT NULL, role text NOT NULL, mention text NOT NULL, match_level text NOT NULL,
  candidates int NOT NULL, certainty text NOT NULL, person_id text, ref_id text, person_name text,
  built_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS person_case_links_cause_idx ON person_case_links (cause_num);
CREATE INDEX IF NOT EXISTS person_case_links_person_idx ON person_case_links (person_id);
