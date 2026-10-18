-- Court decisions matched from the ЄДРСР open-data dumps (src/edrsr_index.py). Texts are fetched on demand.
CREATE TABLE IF NOT EXISTS court_decisions (
    doc_id            text PRIMARY KEY,
    cause_num         text,
    court_code        text,
    court             text,
    judgment_form     text,
    justice_kind      text,
    category          text,
    adjudication_date date,
    receipt_date      date,
    judge             text,
    doc_url           text,
    status            text,
    date_publ         date,
    year              int,
    text              text,
    text_fetched_at   timestamptz,
    text_error        text,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS court_decisions_cause_idx ON court_decisions(cause_num);
CREATE INDEX IF NOT EXISTS court_decisions_court_date_idx ON court_decisions(court_code, adjudication_date);
