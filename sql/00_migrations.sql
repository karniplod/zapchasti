-- Накопившиеся изменения схемы. Применять ПОСЛЕ основных файлов:
--   schema.sql -> vin_patterns.sql -> vin_queries.sql -> catalog_mapping.sql -> этот

-- Внутренние номера доноров: D-0001, D-0002...
CREATE SEQUENCE IF NOT EXISTS donor_code_seq START 1;

-- Атомарный счётчик деталей внутри машины (артикул D-0042-0137)
ALTER TABLE donors ADD COLUMN IF NOT EXISTS part_counter int NOT NULL DEFAULT 0;

-- Чтобы не печатать этикетки повторно
ALTER TABLE parts ADD COLUMN IF NOT EXISTS label_printed_at timestamptz;

-- Последний вход сотрудника
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

-- Миниатюры и размеры: без размеров каталог «прыгает» при загрузке
ALTER TABLE part_photos
    ADD COLUMN IF NOT EXISTS thumb  text,
    ADD COLUMN IF NOT EXISTS width  int,
    ADD COLUMN IF NOT EXISTS height int;

ALTER TABLE donor_photos
    ADD COLUMN IF NOT EXISTS thumb  text,
    ADD COLUMN IF NOT EXISTS width  int,
    ADD COLUMN IF NOT EXISTS height int;

-- Раздел категории на площадке (Авито: «Двигатель», «Система охлаждения»...).
-- Своё дерево остаётся для склада — тут только куда её разместить в фиде.
-- NULL — площадка делит это на несколько своих разделов вне «Для автомобилей»
-- (Мультимедиа/Колёса/Прочее/Климат), однозначного соответствия нет.
ALTER TABLE part_categories ADD COLUMN IF NOT EXISTS avito_category text;

-- Число дверей — влияет на применимость обшивки, стёкол, замков
ALTER TABLE modifications ADD COLUMN IF NOT EXISTS doors smallint;

-- Естественный ключ модификации: своего ID у внешних источников мы не
-- храним, поэтому «та же самая» модификация определяется набором
-- характеристик. Уникальный индекс позволяет импорту вставлять через
-- ON CONFLICT вместо отдельного SELECT на каждую строку файла.
--
-- NULLS NOT DISTINCT (PostgreSQL 15+) обязателен: без него две строки
-- с engine_code IS NULL считались бы разными и дубли бы прошли.
--
-- Первым столбцом идёт generation_id, поэтому индекс заодно обслуживает
-- поиск по одному generation_id (внешний ключ своего индекса не создаёт).
CREATE UNIQUE INDEX IF NOT EXISTS modifications_natural_key_idx
    ON modifications (generation_id, engine_code, transmission, drive, power_hp)
    NULLS NOT DISTINCT;

DROP INDEX IF EXISTS modifications_generation_id_idx;

-- Комплектация (трим): «Комфорт», «Люкс»... У одной модификации их
-- может быть несколько, названия и состав — только свои, никакого
-- внешнего справочника тут нет.
CREATE TABLE IF NOT EXISTS complectations (
    id              serial PRIMARY KEY,
    modification_id int NOT NULL REFERENCES modifications(id) ON DELETE CASCADE,
    name            text NOT NULL,
    sort_order      smallint NOT NULL DEFAULT 0,
    UNIQUE (modification_id, name)
);
