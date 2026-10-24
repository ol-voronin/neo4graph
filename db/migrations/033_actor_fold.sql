-- Auto actor ids per surname were one per spelling: ru:~rybar / ru:~рыбарь, ru:~rozhin / ru:~рожин, and handles
-- (boris_rozhin, @rybar) got none, so one speaker was split on the trust page. The surname is now folded across scripts
-- (the same key as speaker_key() in src/topic_briefs.py): transliterated to Latin, y -> i, h dropped, doubles collapsed,
-- a nominative ending cut. Curated aliases still win.
CREATE OR REPLACE FUNCTION fold_surname(w text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN length(x) > 5 THEN regexp_replace(x, '(ii|ij|i|a|ia)$', '') ELSE x END
  FROM (SELECT regexp_replace(replace(replace(translate(
           replace(replace(replace(replace(replace(replace(replace(replace(
             regexp_replace(lower(coalesce(w, '')), '^.*_', ''),
             'ж', 'zh'), 'х', 'kh'), 'ц', 'ts'), 'ч', 'ch'), 'щ', 'sh'), 'ш', 'sh'), 'ю', 'iu'), 'я', 'ia'),
           'абвгґдеєёэзиіїйыклмнопрстуфьъ''’ʼ', 'abvggdeeeeziiiiiklmnoprstuf'),
         'y', 'i'), 'h', ''), '(.)\1+', '\1', 'g') AS x) t
$$;

CREATE OR REPLACE FUNCTION actor_of(side text, name text, src text) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE
  n text := trim(regexp_replace(coalesce(name, ''), '\s*[,(].*$', ''));
  r text;
  c text := lower(coalesce(nullif(split_part(coalesce(side, ''), '_', 1), ''), 'other'));
BEGIN
  IF n <> '' THEN
    SELECT actor_id INTO r FROM actor_aliases WHERE lower(n) ~* pattern ORDER BY length(pattern) DESC, actor_id LIMIT 1;
    IF r IS NOT NULL THEN RETURN r; END IF;
  END IF;
  IF n = '' AND src IS NOT NULL THEN
    -- the channel's own actor, if the side agrees (a quoted foreign speaker without a name stays unattributed)
    SELECT id INTO r FROM actors WHERE src = ANY(source_ids)
      AND (side IS NULL OR country = split_part(side, '_', 1) OR (country = 'EU' AND side IN ('EU', 'NATO')))
      ORDER BY kind DESC LIMIT 1;  -- 'person' before 'institution'
    RETURN r;
  END IF;
  -- a channel handle as the speaker (boris_rozhin, tg:rybar, @sashakots)
  IF n ~* '^(tg:|@)?[a-z0-9_]{3,}$' THEN
    RETURN c || ':~' || fold_surname(regexp_replace(n, '^(tg:|@)', '', 'i'));
  END IF;
  IF n ~ '^([[:upper:]]\.\s?)*[[:upper:]][[:lower:]''’ʼ]+(-[[:upper:]]?[[:lower:]]+)*(\s+([[:upper:]]\.\s?)*[[:upper:]][[:lower:]''’ʼ]+(-[[:upper:]]?[[:lower:]]+)*){0,2}$'
     AND n !~* '(україн|росі|росс|област|штаб|сил|міністер|министер|канал|посол|голова|президент|служб|офіс|офис|уряд|рада|прес|військ|военн|адміністрац|администрац|командуван|генерал|ministry|office|house|council|department)' THEN
    RETURN c || ':~' || fold_surname(regexp_replace(regexp_replace(n, '([[:upper:]]\.\s?)+', '', 'g'), '^.*\s', ''));
  END IF;
  RETURN NULL;
END $$;

UPDATE statements s SET actor_id = a.id
FROM (SELECT s2.id, actor_of(s2.actor_side, s2.actor_name, m.source_id) id_new FROM statements s2 JOIN messages m ON m.id = s2.message_id
      WHERE s2.actor_id IS NULL OR s2.actor_id LIKE '%:~%') a(sid, id)
WHERE s.id = a.sid AND s.actor_id IS DISTINCT FROM a.id;
