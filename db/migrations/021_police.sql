-- 021: Національна поліція open data (data.gov.ua): persons wanted («особи, які переховуються від органів влади») in full,
-- missing persons only as aggregates. Loaded daily by src/police_wanted.py (.github/workflows/opendata.yml). Idempotent.

-- One row per wanted record as published, plus when the project first / last saw it. A record that disappears from the
-- file gets removed = the first day it was missing (found, detained, case closed or record withdrawn: the file does not say).
CREATE TABLE IF NOT EXISTS wanted_persons (
    id            text PRIMARY KEY,
    category      text,
    last_name_u   text, first_name_u text, middle_name_u text,
    last_name_r   text, first_name_r text, middle_name_r text,
    last_name_e   text, first_name_e text, middle_name_e text,
    name_u        text GENERATED ALWAYS AS (lower(btrim(regexp_replace(coalesce(last_name_u, '') || ' ' || coalesce(first_name_u, '') || ' ' || coalesce(middle_name_u, ''), '\s+', ' ', 'g')))) STORED,
    name_r        text GENERATED ALWAYS AS (lower(btrim(regexp_replace(coalesce(last_name_r, '') || ' ' || coalesce(first_name_r, '') || ' ' || coalesce(middle_name_r, ''), '\s+', ' ', 'g')))) STORED,
    birth_date    date,
    sex           text,
    lost_date     date,              -- date the person went into hiding / was put on the list, as published
    lost_place    text,
    ovd           text,              -- the body searching for the person
    agency        text,              -- police | sbu | dbr | nabu | border_guard | other, from ovd
    region        text,              -- oblast slug (crimea, russia, unknown too) from ovd, else from lost_place
    article_crim  text,
    article       text,              -- first Criminal Code article: '408', '111-1', ...
    art_group     text,              -- war_collab | military_evasion | border | corruption | violent | property | other
    restraint     text,
    first_seen    date NOT NULL DEFAULT current_date,
    last_seen     date NOT NULL DEFAULT current_date,
    removed       date
);
CREATE INDEX IF NOT EXISTS wanted_name_u_idx ON wanted_persons (name_u);
CREATE INDEX IF NOT EXISTS wanted_name_r_idx ON wanted_persons (name_r);
CREATE INDEX IF NOT EXISTS wanted_group_idx ON wanted_persons (art_group, lost_date);

-- Daily snapshot totals, so the trend survives even if a day's file is skipped.
CREATE TABLE IF NOT EXISTS wanted_daily (
    date      date NOT NULL,
    art_group text NOT NULL,
    region    text NOT NULL,
    active    integer NOT NULL,
    added     integer NOT NULL,
    removed   integer NOT NULL,
    PRIMARY KEY (date, art_group, region)
);

-- Missing persons (особи, зниклі безвісти): counts only, no names. By month of disappearance, oblast, sex and age band.
CREATE TABLE IF NOT EXISTS missing_monthly (
    snapshot  date NOT NULL,
    month     date NOT NULL,
    region    text NOT NULL,
    sex       text NOT NULL,
    age_band  text NOT NULL,         -- '<18', '18-24', '25-59', '60+', '?'
    n         integer NOT NULL,
    PRIMARY KEY (snapshot, month, region, sex, age_band)
);

-- Monthly flow of new wanted records by article group (by lost_date), for the analytics block.
CREATE OR REPLACE VIEW wanted_monthly AS
SELECT date_trunc('month', lost_date)::date AS month, art_group, agency, region, count(*) AS n,
       count(*) FILTER (WHERE removed IS NULL) AS still_wanted
FROM wanted_persons
WHERE lost_date BETWEEN '2014-01-01' AND current_date
GROUP BY 1, 2, 3, 4;
