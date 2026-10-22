-- 023: contents of NAZK declarations (public version) for people the registry chain looked up: real estate, vehicles,
-- valuable movables, securities, corporate rights, beneficial ownership, income sources, loans given / taken, memberships,
-- bank accounts' institutions, family members. Filled by local/ua_worker.py through /api/ingest (kind "decl_items").
-- NAZK already hides addresses, registration numbers, VINs, tax numbers and birth dates in the public version.
CREATE TABLE IF NOT EXISTS decl_items (
    decl_id      text NOT NULL,
    idx          integer NOT NULL,          -- position within the declaration
    query        text,                      -- the person query that fetched it (lower case)
    declarant    text NOT NULL,             -- full name as declared, lower case
    year         integer,
    decl_type    integer,                   -- 1 annual, 2 before dismissal, 3 after dismissal, 4 candidate
    step         text NOT NULL,             -- step_3 ...
    kind         text NOT NULL,             -- real_estate | unfinished | vehicle | movable | securities | corporate | beneficial |
                                            -- intangible | income | money | liability | expense | membership | account | family
    label        text NOT NULL,             -- human summary («Квартира 85 м², м. Київ», «BMW X5 2019»)
    owner        text,                      -- who holds it: the declarant or a family member (lower case)
    rel_name     text,                      -- the other party: company or person
    rel_code     text,                      -- its ЄДРПОУ when published
    rel_kind     text,                      -- org | person
    value        numeric,
    currency     text,
    since        text,                      -- acquisition / transaction date as written
    region       text,                      -- oblast slug from the KATOTTG code when published
    details      jsonb,
    created_at   timestamptz DEFAULT now(),
    PRIMARY KEY (decl_id, idx)
);
CREATE INDEX IF NOT EXISTS decl_items_declarant_idx ON decl_items (declarant);
CREATE INDEX IF NOT EXISTS decl_items_rel_code_idx ON decl_items (rel_code) WHERE rel_code IS NOT NULL;
