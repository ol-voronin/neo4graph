-- 024: ARMA «Єдиний державний реєстр активів, на які накладено арешт у кримінальному провадженні» (data.gov.ua), one row per
-- asset (src/arma_assets.py, weekly in workflow opendata; docs/arma.md). No owner names in the open register: an asset links to
-- a person only through its court case numbers (docnums) or ЄРДР numbers (erdr). Idempotent.
CREATE TABLE IF NOT EXISTS arma_assets (
    asset_id        bigint PRIMARY KEY,
    description     text NOT NULL,        -- as published, first 120 chars
    props           text,                 -- raw props (first 120 chars) only when nothing structured was extracted
    kind            text NOT NULL,        -- vehicle | real_estate | land | shares | vessel | aircraft
    brand           text,
    model           text,
    year            integer,
    area            numeric,              -- m² (hectares converted)
    region          text,                 -- oblast slug from the description (kyiv-city, odeska, …), when named
    docnums         text[] NOT NULL DEFAULT '{}',   -- court case numbers of the decisions, lower case
    erdr            text[] NOT NULL DEFAULT '{}',   -- 17-digit ЄРДР numbers
    last_action     text,                 -- latest court decision on it: arrest | arrest_partly | management | sale | forfeited | lifted |
                                          -- lifted_partly | management_cancelled | other (date = last_decision; the court is
                                          -- the first segment of the case number: 757 Печерський, 991 ВАКС …)
    state           text,                 -- latest ARMA action state (management, sale, deposit, returned…)
    valuation       numeric,              -- ARMA valuation, UAH (largest reported)
    income          numeric,              -- ARMA action income, UAH
    first_decision  date,
    last_decision   date,
    units           integer NOT NULL DEFAULT 1,   -- identical lots folded into one row (same description, props, cases)
    updated         date NOT NULL DEFAULT current_date
);
CREATE INDEX IF NOT EXISTS arma_docnums_idx ON arma_assets USING gin (docnums);
CREATE INDEX IF NOT EXISTS arma_erdr_idx ON arma_assets USING gin (erdr);
CREATE INDEX IF NOT EXISTS arma_kind_idx ON arma_assets (kind, last_decision);

-- everything the register holds, by kind and state, including the lots not stored row by row (money, railcars, goods)
CREATE TABLE IF NOT EXISTS arma_counts (
    kind   text NOT NULL,
    state  text,
    n      integer NOT NULL
);
