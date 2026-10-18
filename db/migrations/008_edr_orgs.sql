-- Legal entities from the ЄДР dump that match organisations named in case material (src/edr_match.py).
-- Strong key for the graph (EDRPOU). Founders/beneficiaries/signers are stored as the registry publishes them.
CREATE TABLE IF NOT EXISTS edr_orgs (
    edrpou             text PRIMARY KEY,
    name               text NOT NULL,
    short_name         text,
    stan               text,
    opf                text,
    founders           jsonb NOT NULL DEFAULT '[]',
    beneficiaries      jsonb NOT NULL DEFAULT '[]',
    signers            jsonb NOT NULL DEFAULT '[]',
    authorized_capital text,
    matched_queries    text[] NOT NULL DEFAULT '{}',
    updated_at         timestamptz NOT NULL DEFAULT now()
);
