-- ============================================================
-- Справочник авто: связь с площадками и учёт источника
-- ============================================================
-- Авито и Дром сопоставляют объявления по ТОЧНОМУ названию марки
-- и модели из своего справочника. Своё название и название площадки
-- расходятся («Мерседес» / «Mercedes-Benz», «ВАЗ» / «LADA (ВАЗ)»),
-- поэтому храним оба: своё показываем покупателю, их — отдаём в фид.

ALTER TABLE brands
    ADD COLUMN avito_name text,
    ADD COLUMN drom_name  text,
    ADD COLUMN source     text NOT NULL DEFAULT 'manual';

ALTER TABLE models
    ADD COLUMN avito_name text,
    ADD COLUMN drom_name  text,
    ADD COLUMN source     text NOT NULL DEFAULT 'manual';

ALTER TABLE generations
    ADD COLUMN avito_name text,
    ADD COLUMN drom_name  text,
    ADD COLUMN source     text NOT NULL DEFAULT 'manual';

CREATE INDEX ON brands (avito_name);
CREATE INDEX ON models (avito_name);

-- Поколение, заведённое приёмщиком на бегу, требует проверки:
-- он мог ошибиться в годах или продублировать существующее.
ALTER TABLE generations ADD COLUMN needs_review boolean NOT NULL DEFAULT false;
ALTER TABLE models      ADD COLUMN needs_review boolean NOT NULL DEFAULT false;

-- Журнал импортов: что и когда залили, чтобы понимать,
-- откуда взялась кривая строка
CREATE TABLE import_log (
    id          serial PRIMARY KEY,
    source      text NOT NULL,           -- avito / drom / wikidata / manual
    filename    text,
    brands_new      int NOT NULL DEFAULT 0,
    models_new      int NOT NULL DEFAULT 0,
    generations_new int NOT NULL DEFAULT 0,
    rows_total      int NOT NULL DEFAULT 0,
    rows_skipped    int NOT NULL DEFAULT 0,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

-- Требуется для ON CONFLICT в импортёре: поколение уникально
-- в пределах модели по названию (ограничение уже есть в schema.sql,
-- дублируем проверку на случай ручных правок)
-- UNIQUE (model_id, name) — см. generations

-- Что приёмщик завёл руками и никто не проверил
CREATE OR REPLACE VIEW reference_review AS
SELECT g.id, b.name AS brand, m.name AS model, g.name AS generation,
       g.body_type, g.year_from, g.year_to, g.source,
       (SELECT count(*) FROM donors d WHERE d.generation_id = g.id) AS donors
  FROM generations g
  JOIN models m ON m.id = g.model_id
  JOIN brands b ON b.id = m.brand_id
 WHERE g.needs_review
 ORDER BY donors DESC, b.name, m.name;
