-- 018: search_names without placeholder names. ЄДР founder/beneficiary lines and LLM extractions carry "НЕМАЄ",
-- "відсутній", "-" etc.; 013 indexed them as a person/org "немає" (and /api/graph made it the most connected node).
-- Same definition as 013 plus the placeholder filter (keep in sync with PLACEHOLDER in web/api/_graph.js). NOT YET APPLIED.
DROP MATERIALIZED VIEW IF EXISTS search_names;
CREATE MATERIALIZED VIEW search_names AS
WITH raw(kind, label) AS (
  SELECT 'person', name_norm FROM ee_persons
  UNION ALL SELECT 'person', linked_person FROM ee_assets WHERE linked_person IS NOT NULL
  UNION ALL SELECT 'person', name_norm FROM media_links
  UNION ALL SELECT 'org', name FROM ee_orgs
  UNION ALL SELECT 'case', coalesce(nickname, ref) FROM ee_cases WHERE coalesce(nickname, ref) IS NOT NULL
  UNION ALL SELECT CASE WHEN split_part(f, ';', 2) ~ '(^|\D)\d{8}(\D|$)' THEN 'org' ELSE 'person' END, split_part(f, ';', 1)
    FROM edr_orgs, jsonb_array_elements_text(founders || signers || beneficiaries) f WHERE jsonb_array_length(founders) <= 60
  UNION ALL SELECT CASE WHEN query ~* '^(тов|пат|прат|ат|дп|кп)\M' THEN 'org' ELSE 'person' END, query
    FROM registry_hits WHERE qkind IS DISTINCT FROM 'case' AND query !~ '^\d'
  UNION ALL SELECT src_kind, src_name FROM analyst_links WHERE src_kind IN ('person', 'org')
  UNION ALL SELECT dst_kind, dst_name FROM analyst_links WHERE dst_kind IN ('person', 'org')
)
SELECT kind, lower(n) AS key, min(n) AS label, count(*)::int AS n
FROM (SELECT kind, btrim(regexp_replace(label, '\s+', ' ', 'g')) AS n FROM raw) x
WHERE n <> '' AND length(n) <= 300
  AND lower(regexp_replace(regexp_replace(n, '[«»"“”„]', '', 'g'), '^(тов|пп|фоп)\s+', '', 'i')) !~
      '^(немає|нема|немає даних|відсутн[а-яії]*|не встановлено|не визначено|не вказано|невідом[а-яії]*|нет|нету|отсутству[а-яє]*|не указан[а-я]*|неизвест[а-я]*|н/?а|n/?a|none|null|unknown|[-–—.х_x0 ]+)$'
GROUP BY 1, 2;
CREATE UNIQUE INDEX search_names_pk ON search_names (kind, key);   -- needed for REFRESH ... CONCURRENTLY
CREATE INDEX search_names_trgm ON search_names USING gin (key gin_trgm_ops);
