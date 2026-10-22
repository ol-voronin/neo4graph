-- 025: property named in court decisions (mostly arrest rulings): what was arrested, in which proceeding. The suspect is
-- depersonalised in the register («ОСОБА_5»), so the link to a person goes through the case / ЄРДР proceeding, never the
-- name. Filled by src/case_assets.py. Idempotent.

-- ЄРДР proceeding numbers (17 digits) named in a decision: they tie the investigating judge's rulings (each with its own
-- case number) to the trial case and to ARMA's register (arma_assets.erdr, migration 024).
ALTER TABLE court_decisions ADD COLUMN IF NOT EXISTS erdr text[];
CREATE INDEX IF NOT EXISTS court_decisions_erdr_idx ON court_decisions USING gin (erdr);
UPDATE court_decisions d SET erdr = x.e
FROM (SELECT doc_id, array_agg(DISTINCT m[1]) e FROM court_decisions, regexp_matches(text, '\m(\d{17})\M', 'g') m WHERE text IS NOT NULL GROUP BY 1) x
WHERE d.doc_id = x.doc_id AND d.erdr IS NULL;

CREATE TABLE IF NOT EXISTS case_assets (
    doc_id        text NOT NULL,             -- court_decisions.doc_id
    idx           integer NOT NULL,
    cause_num     text,
    erdr          text[],
    decision_date date,
    kind          text NOT NULL,             -- vehicle | real_estate | land | money | account | shares | company | weapon | other
    label         text NOT NULL,             -- «AUDI Q8 2019», «1/4 квартири 99,3 м²»
    action        text,                      -- arrested | arrest_lifted | confiscated | seized | returned | named
    holder        text,                      -- as written: «ОСОБА_5», a company, «підозрюваний»
    brand text, model text, year integer, area numeric, share text,
    reg_number    text,                      -- real estate register number, when not depersonalised
    cadastral     text,
    bank          text,
    edrpou        text,
    amount        numeric,
    currency      text,
    details       jsonb,
    created_at    timestamptz DEFAULT now(),
    PRIMARY KEY (doc_id, idx)
);
CREATE INDEX IF NOT EXISTS case_assets_cause_idx ON case_assets (lower(cause_num));
CREATE INDEX IF NOT EXISTS case_assets_erdr_idx ON case_assets USING gin (erdr);

-- which decisions were read (so an empty result is not re-read)
CREATE TABLE IF NOT EXISTS asset_extractions (doc_id text PRIMARY KEY, n integer NOT NULL, model text, created_at timestamptz DEFAULT now());
