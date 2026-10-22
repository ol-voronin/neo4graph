-- Fact-check levels (docs/factcheck.md "Країна · Групи · Людина"): canonical actors with ru/uk/en aliases, actor groups,
-- statements.actor_id filled by a trigger, n_checks for re-resolving 'unresolvable' later. Small config tables only;
-- trust scores are computed in the API from counts (web/api/_trust.js), nothing heavy is stored.

-- Groups: a statement's group = its actor's group, else the group whose sides contain statements.actor_side.
-- country: UA | RU | US | EU (EU institutions, NATO, European governments incl. UK) | other.
CREATE TABLE IF NOT EXISTS actor_groups (
  id      text PRIMARY KEY,
  name_uk text NOT NULL,
  country text NOT NULL,
  sides   text[] NOT NULL DEFAULT '{}',
  ord     int NOT NULL DEFAULT 100
);
INSERT INTO actor_groups (id, name_uk, country, sides, ord) VALUES
  ('ru_officials',  'РФ: посадовці й відомства',   'RU', '{RU_official}', 10),
  ('ru_media',      'РФ: державні медіа',           'RU', '{RU_media}', 11),
  ('ru_milbloggers','РФ: воєнкори',                 'RU', '{RU_milblogger}', 12),
  ('ua_officials',  'Україна: посадовці й відомства','UA', '{UA_official}', 20),
  ('ua_media',      'Україна: медіа',               'UA', '{UA_media}', 21),
  ('us_admin',      'США: адміністрація',           'US', '{US_admin}', 30),
  ('us_politics',   'США: Конгрес і політики',      'US', '{US_right,US_left,US_congress_bipartisan,US_other}', 31),
  ('eu_nato',       'ЄС, НАТО, Європа',             'EU', '{EU,NATO}', 40),
  ('other',         'Інші',                         'other', '{other}', 90)
ON CONFLICT (id) DO UPDATE SET name_uk = EXCLUDED.name_uk, country = EXCLUDED.country, sides = EXCLUDED.sides, ord = EXCLUDED.ord;

-- Canonical actors. source_ids: channels that speak as this actor (a statement with no actor_name from such a channel
-- belongs to it). graph_name: how the person is spelled in the graph (for the «Профіль» link).
CREATE TABLE IF NOT EXISTS actors (
  id         text PRIMARY KEY,           -- 'ru:lavrov', 'ua:zelensky', 'us:trump', 'ru:mid'
  name_uk    text NOT NULL,
  name_en    text,
  kind       text NOT NULL DEFAULT 'person' CHECK (kind IN ('person', 'institution')),
  country    text NOT NULL,
  grp        text REFERENCES actor_groups(id),
  role_uk    text,
  source_ids text[] NOT NULL DEFAULT '{}',
  graph_name text
);
-- Aliases: case-insensitive POSIX regexes over the speaker name with role suffixes cut ("Лавров (МЗС РФ)" -> "лавров").
-- Longest matching pattern wins. Add a row + re-run the backfill UPDATE at the end of this file to merge spellings.
CREATE TABLE IF NOT EXISTS actor_aliases (
  actor_id text NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
  pattern  text NOT NULL,
  PRIMARY KEY (actor_id, pattern)
);

INSERT INTO actors (id, name_uk, name_en, kind, country, grp, role_uk, source_ids, graph_name) VALUES
  ('ru:putin',      'Володимир Путін',     'Vladimir Putin',     'person', 'RU', 'ru_officials', 'президент РФ', '{tg:news_kremlin}', NULL),
  ('ru:peskov',     'Дмитро Пєсков',       'Dmitry Peskov',      'person', 'RU', 'ru_officials', 'прессекретар Кремля', '{}', NULL),
  ('ru:lavrov',     'Сергій Лавров',       'Sergey Lavrov',      'person', 'RU', 'ru_officials', 'міністр закордонних справ РФ', '{}', NULL),
  ('ru:zakharova',  'Марія Захарова',      'Maria Zakharova',    'person', 'RU', 'ru_officials', 'речниця МЗС РФ', '{}', NULL),
  ('ru:medvedev',   'Дмитро Медведєв',     'Dmitry Medvedev',    'person', 'RU', 'ru_officials', 'заступник голови Радбезу РФ', '{tg:medvedev_telegram}', NULL),
  ('ru:shoigu',     'Сергій Шойгу',        'Sergei Shoigu',      'person', 'RU', 'ru_officials', 'секретар Радбезу РФ', '{}', NULL),
  ('ru:belousov',   'Андрій Бєлоусов',     'Andrei Belousov',    'person', 'RU', 'ru_officials', 'міністр оборони РФ', '{}', NULL),
  ('ru:gerasimov',  'Валерій Герасимов',   'Valery Gerasimov',   'person', 'RU', 'ru_officials', 'начальник Генштабу РФ', '{}', NULL),
  ('ru:ushakov',    'Юрій Ушаков',         'Yuri Ushakov',       'person', 'RU', 'ru_officials', 'помічник президента РФ', '{}', NULL),
  ('ru:dmitriev',   'Кирило Дмитрієв',     'Kirill Dmitriev',    'person', 'RU', 'ru_officials', 'спецпредставник РФ', '{}', NULL),
  ('ru:nebenzya',   'Василь Небензя',      'Vasily Nebenzya',    'person', 'RU', 'ru_officials', 'постпред РФ в ООН', '{}', NULL),
  ('ru:volodin',    'Вʼячеслав Володін',   'Vyacheslav Volodin', 'person', 'RU', 'ru_officials', 'голова Держдуми', '{}', NULL),
  ('ru:mid',        'МЗС Росії',           'Russian MFA',        'institution', 'RU', 'ru_officials', NULL, '{tg:MID_Russia}', NULL),
  ('ru:mod',        'Міноборони РФ',       'Russian MoD',        'institution', 'RU', 'ru_officials', NULL, '{tg:mod_russia}', NULL),
  ('ru:kremlin',    'Кремль',              'Kremlin',            'institution', 'RU', 'ru_officials', NULL, '{}', NULL),
  ('ru:solovyov',   'Володимир Соловйов',  'Vladimir Solovyov',  'person', 'RU', 'ru_media', 'ведучий держТБ', '{tg:SolovievLive}', NULL),
  ('ru:simonyan',   'Маргарита Симоньян',  'Margarita Simonyan', 'person', 'RU', 'ru_media', 'головред RT', '{}', NULL),
  ('ua:zelensky',   'Володимир Зеленський','Volodymyr Zelensky', 'person', 'UA', 'ua_officials', 'президент України', '{tg:V_Zelenskiy_official}', 'Зеленський Володимир Олександрович'),
  ('ua:sybiha',     'Андрій Сибіга',       'Andrii Sybiha',      'person', 'UA', 'ua_officials', 'міністр закордонних справ', '{}', 'Сибіга Андрій Іванович'),
  ('ua:syrskyi',    'Олександр Сирський',  'Oleksandr Syrskyi',  'person', 'UA', 'ua_officials', 'головнокомандувач ЗСУ', '{}', 'Сирський Олександр Станіславович'),
  ('ua:budanov',    'Кирило Буданов',      'Kyrylo Budanov',     'person', 'UA', 'ua_officials', NULL, '{}', 'Буданов Кирило Олексійович'),
  ('ua:umerov',     'Рустем Умєров',       'Rustem Umerov',      'person', 'UA', 'ua_officials', NULL, '{}', 'Умєров Рустем Енверович'),
  ('ua:svyrydenko', 'Юлія Свириденко',     'Yulia Svyrydenko',   'person', 'UA', 'ua_officials', NULL, '{}', 'Свириденко Юлія Анатоліївна'),
  ('ua:shmyhal',    'Денис Шмигаль',       'Denys Shmyhal',      'person', 'UA', 'ua_officials', NULL, '{}', 'Шмигаль Денис Анатолійович'),
  ('ua:yermak',     'Андрій Єрмак',        'Andriy Yermak',      'person', 'UA', 'ua_officials', NULL, '{}', 'Єрмак Андрій Борисович'),
  ('ua:ihnat',      'Юрій Ігнат',          'Yurii Ihnat',        'person', 'UA', 'ua_officials', 'речник Повітряних сил', '{}', 'Ігнат Юрій Анатолійович'),
  ('ua:synehubov',  'Олег Синєгубов',      'Oleh Syniehubov',    'person', 'UA', 'ua_officials', 'голова Харківської ОВА', '{tg:synegubov}', 'Синєгубов Олег Васильович'),
  ('ua:genstaff',   'Генштаб ЗСУ',         'General Staff of Ukraine', 'institution', 'UA', 'ua_officials', NULL, '{tg:GeneralStaffZSU}', NULL),
  ('ua:airforce',   'Повітряні сили ЗСУ',  'Ukrainian Air Force','institution', 'UA', 'ua_officials', NULL, '{tg:kpszsu}', NULL),
  ('ua:op',         'Офіс Президента',     'Office of the President', 'institution', 'UA', 'ua_officials', NULL, '{}', NULL),
  ('us:trump',      'Дональд Трамп',       'Donald Trump',       'person', 'US', 'us_admin', 'президент США', '{rss:whitehouse}', NULL),
  ('us:vance',      'Джей Ді Венс',        'JD Vance',           'person', 'US', 'us_admin', 'віцепрезидент США', '{}', NULL),
  ('us:rubio',      'Марко Рубіо',         'Marco Rubio',        'person', 'US', 'us_admin', 'держсекретар США', '{rss:state_dept}', NULL),
  ('us:hegseth',    'Піт Гегсет',          'Pete Hegseth',       'person', 'US', 'us_admin', 'міністр оборони США', '{}', NULL),
  ('us:witkoff',    'Стів Віткофф',        'Steve Witkoff',      'person', 'US', 'us_admin', 'спецпосланець США', '{}', NULL),
  ('us:kellogg',    'Кіт Келлог',          'Keith Kellogg',      'person', 'US', 'us_admin', 'спецпосланець США', '{}', NULL),
  ('us:kushner',    'Джаред Кушнер',       'Jared Kushner',      'person', 'US', 'us_admin', NULL, '{}', NULL),
  ('us:leavitt',    'Керолайн Левітт',     'Karoline Leavitt',   'person', 'US', 'us_admin', 'прессекретарка Білого дому', '{}', NULL),
  ('us:graham',     'Ліндсі Грем',         'Lindsey Graham',     'person', 'US', 'us_politics', 'сенатор', '{}', NULL),
  ('eu:rutte',      'Марк Рютте',          'Mark Rutte',         'person', 'EU', 'eu_nato', 'генсек НАТО', '{}', NULL),
  ('eu:vonderleyen','Урсула фон дер Ляєн', 'Ursula von der Leyen','person', 'EU', 'eu_nato', 'президентка Єврокомісії', '{}', NULL),
  ('eu:kallas',     'Кая Каллас',          'Kaja Kallas',        'person', 'EU', 'eu_nato', 'верховна представниця ЄС', '{}', NULL),
  ('eu:macron',     'Емманюель Макрон',    'Emmanuel Macron',    'person', 'EU', 'eu_nato', 'президент Франції', '{}', NULL),
  ('eu:merz',       'Фрідріх Мерц',        'Friedrich Merz',     'person', 'EU', 'eu_nato', 'канцлер Німеччини', '{}', NULL),
  ('eu:starmer',    'Кір Стармер',         'Keir Starmer',       'person', 'EU', 'eu_nato', 'премʼєр Британії', '{}', NULL),
  ('eu:uk_mod',     'Міноборони Британії', 'UK Ministry of Defence', 'institution', 'EU', 'eu_nato', NULL, '{rss:uk_mod}', NULL),
  ('other:erdogan', 'Реджеп Ердоган',      'Recep Erdogan',      'person', 'other', 'other', 'президент Туреччини', '{}', NULL),
  ('other:lukashenko','Олександр Лукашенко','Alexander Lukashenko','person','other', 'other', NULL, '{}', NULL)
ON CONFLICT (id) DO UPDATE SET name_uk = EXCLUDED.name_uk, name_en = EXCLUDED.name_en, kind = EXCLUDED.kind, country = EXCLUDED.country,
  grp = EXCLUDED.grp, role_uk = EXCLUDED.role_uk, source_ids = EXCLUDED.source_ids, graph_name = EXCLUDED.graph_name;

INSERT INTO actor_aliases (actor_id, pattern) VALUES
  ('ru:putin', '(путін|путин|putin)'), ('ru:peskov', '(пєсков|песков|peskov)'), ('ru:lavrov', '(лавров|lavrov)'),
  ('ru:zakharova', '(захаров[аоу]|zakharova)'), ('ru:medvedev', '(медведєв|медведев|medvedev)'), ('ru:shoigu', '(шойгу|shoigu)'),
  ('ru:belousov', '(бєлоусов|белоусов|belousov)'), ('ru:gerasimov', '(герасимов|gerasimov)'), ('ru:ushakov', '(ушаков|ushakov)'),
  ('ru:dmitriev', '(дмитрієв|дмитриев|dmitriev)'), ('ru:nebenzya', '(небенз|nebenz)'), ('ru:volodin', '(володін|володин|volodin)'),
  ('ru:mid', '(mid_russia|^мид\M|^мід\M|^мзс (рф|росі)|^мид (рф|росс)|^міністерство закордонних справ (рф|росі)|^министерств\w* иностранн|russian (mfa|foreign ministry))'),
  ('ru:mod', '(mod_russia|^минобороны|^міноборони (рф|росі)|^министерств\w* обороны|^міністерство оборони (рф|росі)|russian (mod|defen[cs]e ministry))'),
  ('ru:kremlin', '^(кремль|kremlin)$'),
  ('ru:solovyov', '(соловйов|соловьев|соловьёв|solovyov|soloviev)'), ('ru:simonyan', '(симоньян|simonyan)'),
  ('ua:zelensky', '(зеленськ|зеленск|zelensk|^президент україни$|^президент украины$)'), ('ua:sybiha', '(сибіг|сибиг|sybiha|sybiga)'),
  ('ua:syrskyi', '(сирськ|сырск|syrsk)'), ('ua:budanov', '(буданов|budanov)'), ('ua:umerov', '(умєров|умеров|umerov)'),
  ('ua:svyrydenko', '(свириденк|svyrydenk)'), ('ua:shmyhal', '(шмигал|шмыгал|shmyhal)'), ('ua:yermak', '(єрмак|ермак|yermak)'),
  ('ua:ihnat', '\m(ігнат|игнат|ihnat)\M'), ('ua:synehubov', '(синєгубов|синегубов|syniehubov|synehubov)'),
  ('ua:genstaff', '(generalstaff|генштаб|генеральн\w* штаб|general staff)'), ('ua:airforce', '(повітрян\w* сил|воздушн\w* сил|air force|kpszsu)'),
  ('ua:op', '(офіс президента|офис президента)'),
  ('us:trump', '(трамп|trump)'), ('us:vance', '\m(венс|вэнс|vance)\M'), ('us:rubio', '(рубіо|рубио|rubio)'), ('us:hegseth', '(гегсет|хегсет|hegseth)'),
  ('us:witkoff', '(віткоф|уиткоф|уиткофф|witkoff)'), ('us:kellogg', '(келлог|kellogg)'), ('us:kushner', '\m(кушнер|kushner)\M'),
  ('us:leavitt', '(левітт|ливитт|левитт|leavitt)'), ('us:graham', '\m(грем|грэм|graham)$'),
  ('eu:rutte', '(рютте|rutte)'), ('eu:vonderleyen', '(ляєн|ляйен|leyen)'), ('eu:kallas', '(каллас|kallas)'), ('eu:macron', '(макрон|macron)'),
  ('eu:merz', '\m(мерц|merz)\M'), ('eu:starmer', '(стармер|starmer)'),
  ('eu:uk_mod', '(uk ministry of defen|міноборони (британ|великобритан)|минобороны великобритан|british (mod|defen))'),
  ('other:erdogan', '(ердоган|эрдоган|erdogan|erdoğan)'), ('other:lukashenko', '(лукашенк|lukashenk)')
ON CONFLICT DO NOTHING;

-- Speaker -> actor id. Order: alias regex (longest pattern first); empty name -> the channel's own actor; a person-like
-- name (1–3 capitalised words, no institution words) -> '<country>:~<surname>' so one person still aggregates without a
-- curated row; otherwise NULL (the statement still counts for its group and country).
CREATE OR REPLACE FUNCTION actor_of(side text, name text, src text) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE
  n text := trim(regexp_replace(coalesce(name, ''), '\s*[,(].*$', ''));
  r text;
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
  IF n ~ '^([[:upper:]]\.\s?)*[[:upper:]][[:lower:]''’ʼ]+(-[[:upper:]]?[[:lower:]]+)*(\s+([[:upper:]]\.\s?)*[[:upper:]][[:lower:]''’ʼ]+(-[[:upper:]]?[[:lower:]]+)*){0,2}$'
     AND n !~* '(україн|росі|росс|област|штаб|сил|міністер|министер|канал|посол|голова|президент|служб|офіс|офис|уряд|рада|прес|військ|военн|адміністрац|администрац|командуван|генерал|ministry|office|house|council|department)' THEN
    RETURN lower(coalesce(nullif(split_part(coalesce(side, ''), '_', 1), ''), 'other')) || ':~'
           || lower(regexp_replace(regexp_replace(n, '([[:upper:]]\.\s?)+', '', 'g'), '^.*\s', ''));
  END IF;
  RETURN NULL;
END $$;

ALTER TABLE statements ADD COLUMN IF NOT EXISTS actor_id text;
ALTER TABLE statements ADD COLUMN IF NOT EXISTS n_checks smallint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS statements_actor_idx ON statements (actor_id, ts_published);

CREATE OR REPLACE FUNCTION statements_set_actor() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.actor_id := actor_of(NEW.actor_side, NEW.actor_name, (SELECT source_id FROM messages WHERE id = NEW.message_id));
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS statements_actor_trg ON statements;
CREATE TRIGGER statements_actor_trg BEFORE INSERT OR UPDATE OF actor_name, actor_side ON statements
  FOR EACH ROW EXECUTE FUNCTION statements_set_actor();

-- contradictions was created by src/find_contradictions.py; make it a migration-owned table with actor_id.
CREATE TABLE IF NOT EXISTS contradictions (
  id bigserial PRIMARY KEY, actor_key text, actor_side text, actor_name text,
  a_statement bigint REFERENCES statements(id) ON DELETE CASCADE, b_statement bigint REFERENCES statements(id) ON DELETE CASCADE,
  b_event bigint, kind text, topic text, explanation text, confidence real, model text, created_at timestamptz DEFAULT now(),
  UNIQUE (a_statement, b_statement, b_event));
ALTER TABLE contradictions ADD COLUMN IF NOT EXISTS actor_id text;
CREATE INDEX IF NOT EXISTS contradictions_actor_idx ON contradictions (actor_id);
-- last contradiction pass per actor (find_contradictions.py skips actors with no newer statements)
CREATE TABLE IF NOT EXISTS contradiction_passes (actor_key text PRIMARY KEY, checked_at timestamptz NOT NULL);

-- Backfill (re-run these two after editing aliases).
UPDATE statements s SET actor_id = actor_of(s.actor_side, s.actor_name, m.source_id) FROM messages m WHERE m.id = s.message_id;
UPDATE contradictions c SET actor_id = s.actor_id FROM statements s WHERE s.id = c.a_statement AND c.actor_id IS DISTINCT FROM s.actor_id;

-- One row per statement with its actor, group and country (the API aggregates this; nothing is materialised).
CREATE OR REPLACE VIEW statements_x AS
SELECT s.id, s.message_id, s.ts_published, s.actor_side, s.actor_name, s.actor_id, s.stype, s.quote, s.predicted_event, s.target_region,
       s.horizon_days, s.resolvable, s.status, s.resolved_at, s.evidence_ids, s.resolver_note, m.source_id, m.url,
       coalesce(a.grp, g.id, 'other') AS grp,
       coalesce(a.country, g.country, CASE src.country WHEN 'UA' THEN 'UA' WHEN 'RU' THEN 'RU' WHEN 'US' THEN 'US' ELSE 'other' END) AS country
FROM statements s
JOIN messages m ON m.id = s.message_id
LEFT JOIN sources src ON src.id = m.source_id
LEFT JOIN actors a ON a.id = s.actor_id
LEFT JOIN LATERAL (SELECT id, country FROM actor_groups WHERE s.actor_side = ANY(sides) ORDER BY ord LIMIT 1) g ON true;
