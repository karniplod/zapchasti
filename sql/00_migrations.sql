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

-- Комплектация принятой машины. Не обязательна: у многих модификаций
-- её в справочнике нет, а приёмщик не всегда может определить трим
-- по кузову — тогда поле остаётся пустым.
ALTER TABLE donors ADD COLUMN IF NOT EXISTS complectation_id int
    REFERENCES complectations(id);

-- Деталь, поступившая отдельно от машины (выкуплена, привезена под
-- заказ, новая). Схема изначально требовала донора для каждой детали,
-- поэтому «Принять запчасть» не могла сохранить ничего.
ALTER TABLE parts ALTER COLUMN donor_id DROP NOT NULL;
ALTER TABLE parts ADD COLUMN IF NOT EXISTS source text;

-- Свой артикул для деталей без машины: P-0001. У снятых с донора
-- артикул другой — номер машины плюс счётчик (D-0042-0137).
CREATE SEQUENCE IF NOT EXISTS standalone_part_seq START 1;

-- Ветка дерева, оставленная под будущее наполнение (Прицепы,
-- Для мототехники, Масла и автохимия). Детей у неё нет, поэтому
-- в подборе категории она выглядела как обычная конечная категория —
-- разборщик мог положить деталь в «GPS-навигаторы».
ALTER TABLE part_categories
    ADD COLUMN IF NOT EXISTS is_placeholder boolean NOT NULL DEFAULT false;

-- ------------------------------------------------------------
-- Филиалы
-- ------------------------------------------------------------
-- Город — поле филиала, а не своя таблица: филиала без города не
-- бывает, а для страниц вида «запчасти в Перми» хватает группировки.
CREATE TABLE IF NOT EXISTS branches (
    id         serial PRIMARY KEY,
    city       text NOT NULL,
    name       text NOT NULL,          -- «Ленина 1»
    address    text,
    phone      text,
    is_active  boolean NOT NULL DEFAULT true,
    sort_order smallint NOT NULL DEFAULT 0,
    UNIQUE (city, name)
);

INSERT INTO branches (city, name, sort_order) VALUES
    ('Пермь',  'Ленина 1',                1),
    ('Пермь',  'Комсомольская площадь 1', 2),
    ('Москва', 'Дзержинского 1',          3),
    ('Москва', 'Ушакова 1',               4)
ON CONFLICT (city, name) DO NOTHING;

-- Филиал хранится и у машины, и у детали, и это не дублирование:
-- машину разобрали в Перми, а деталь увезли в Москву под заказ.
-- У машины — где разобрали (факт истории), у детали — где лежит
-- сейчас (то, что видит покупатель). Поле location остаётся полкой
-- внутри филиала: филиал + место = полный адрес детали.
ALTER TABLE donors ADD COLUMN IF NOT EXISTS branch_id int REFERENCES branches(id);
ALTER TABLE parts  ADD COLUMN IF NOT EXISTS branch_id int REFERENCES branches(id);

-- Филиал сотрудника подставляется при приёмке: выбранный руками
-- рано или поздно поставят не тот
ALTER TABLE users  ADD COLUMN IF NOT EXISTS branch_id int REFERENCES branches(id);

CREATE INDEX IF NOT EXISTS parts_branch_id_idx  ON parts (branch_id);
CREATE INDEX IF NOT EXISTS donors_branch_id_idx ON donors (branch_id);

-- Заведённое до появления филиалов приписываем первому по порядку:
-- иначе эти машины и детали выпадут из любой выборки по филиалу
UPDATE donors SET branch_id = (SELECT id FROM branches ORDER BY sort_order LIMIT 1)
 WHERE branch_id IS NULL;
UPDATE parts  SET branch_id = (SELECT id FROM branches ORDER BY sort_order LIMIT 1)
 WHERE branch_id IS NULL;
