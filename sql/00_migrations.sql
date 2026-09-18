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


-- ------------------------------------------------------------
-- Кроссы OEM-номеров
-- ------------------------------------------------------------
-- Поиск в каталоге умеет искать по аналогу: покупатель вводит номер
-- от своего производителя, а на складе лежит номер другого. Запрос
-- в app/routers/catalog.py это делал всегда, но таблиц под него
-- не существовало ни в схеме, ни в миграциях — любой поиск
-- по строке падал с UndefinedTable и отдавал 500.

-- Один артикул TecDoc = один физический аналог, у него несколько
-- номеров разных брендов. Два обращения к таблице по art_id и дают
-- переход «чужой номер -> наш»
CREATE TABLE IF NOT EXISTS oem_cross (
    art_id   bigint NOT NULL,
    code     text   NOT NULL,          -- уже нормализован: только буквы и цифры
    brand    text,
    name_en  text,
    is_oe    boolean NOT NULL DEFAULT true,
    node     text                      -- узел по названию, см. scripts/nodes.py
);
-- Поиск идёт по номеру, схлопывание аналогов — по артикулу
CREATE INDEX IF NOT EXISTS oem_cross_code_idx   ON oem_cross (code);
CREATE INDEX IF NOT EXISTS oem_cross_art_id_idx ON oem_cross (art_id);
-- Один и тот же номер приходит из нескольких файлов выгрузки
CREATE UNIQUE INDEX IF NOT EXISTS oem_cross_uniq ON oem_cross (art_id, code);

-- По каким номерам уже ходили в архив. Архив статичный, повторный
-- проход даст то же самое, а идёт он часами
CREATE TABLE IF NOT EXISTS oem_cross_lookup (
    code       text PRIMARY KEY,
    found      int  NOT NULL DEFAULT 0,
    checked_at timestamptz NOT NULL DEFAULT now()
);

-- Номера, приписанные детали вручную сверх основного oem_number:
-- у детали бывает номер по каталогу производителя и номер на самой
-- отливке, искать надо по обоим
CREATE TABLE IF NOT EXISTS part_oem (
    id      bigserial PRIMARY KEY,
    part_id bigint NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    code    text   NOT NULL,
    UNIQUE (part_id, code)
);

-- Узел категории: тормоза, подвеска, кузов. Нужен, чтобы кросс
-- по номеру не подсунул интеркулер вместо подшипника — один номер
-- встречается у разных производителей на разные детали.
-- Заполняется через python -m app.scripts.set_nodes
ALTER TABLE part_categories ADD COLUMN IF NOT EXISTS node text;


-- ------------------------------------------------------------
-- Личный кабинет покупателя
-- ------------------------------------------------------------
-- Покупатель в схеме был (customers), но войти ему было нечем:
-- строка заводилась бы менеджером при заказе по телефону.
-- Пароль здесь отдельный от сотрудников: это разные люди, разные
-- сессии и разные права, общая таблица users им не подходит.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS password_hash text;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

-- Корзина.
-- Деталь штучная, поэтому строка корзины = одна деталь, без количества.
-- cart_token — кука браузера: корзину собирают до входа, а привязывают
-- к покупателю в момент входа. Без этого всё, что человек выбрал,
-- пропадало бы на форме регистрации.
CREATE TABLE IF NOT EXISTS cart_items (
    id          bigserial PRIMARY KEY,
    cart_token  text   NOT NULL,
    customer_id bigint REFERENCES customers(id) ON DELETE CASCADE,
    part_id     bigint NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    added_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cart_token, part_id)
);
CREATE INDEX IF NOT EXISTS cart_items_customer_idx ON cart_items (customer_id);

-- Номер заказа человеку, а не id из базы: его диктуют по телефону
CREATE SEQUENCE IF NOT EXISTS order_number_seq START 1;

-- Заказ уже есть в схеме, но покупателя в нём не было видно с витрины
CREATE INDEX IF NOT EXISTS orders_customer_idx ON orders (customer_id, created_at DESC);

-- История поиска.
-- VIN-запросы пишутся в vin_queries с самого начала, но обезличенно —
-- для отчёта о спросе. Чтобы показать человеку его собственные поиски,
-- добавляем ссылку на покупателя: у анонимного она пустая, и такой
-- запрос виден только в отчёте.
ALTER TABLE vin_queries ADD COLUMN IF NOT EXISTS customer_id bigint
    REFERENCES customers(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS vin_queries_customer_idx
    ON vin_queries (customer_id, created_at DESC) WHERE customer_id IS NOT NULL;

-- Поиск по названию и номеру своей таблицы не имел вовсе
CREATE TABLE IF NOT EXISTS search_queries (
    id            bigserial PRIMARY KEY,
    customer_id   bigint REFERENCES customers(id) ON DELETE SET NULL,
    query         text NOT NULL,
    results_count int  NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS search_queries_customer_idx
    ON search_queries (customer_id, created_at DESC) WHERE customer_id IS NOT NULL;

-- Оплата.
-- Способы оплаты пока не подключены, но заказ уже проходит через
-- попытку оплаты: провайдер добавляется строкой в PAYMENT_METHODS
-- и обработчиком, схема при этом не меняется.
CREATE TABLE IF NOT EXISTS payments (
    id          bigserial PRIMARY KEY,
    order_id    bigint NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    method      text   NOT NULL,          -- card / sbp / invoice / cash
    amount      numeric(12,2) NOT NULL,
    status      text   NOT NULL DEFAULT 'pending',  -- pending / paid / failed
    external_id text,                     -- идентификатор на стороне банка
    created_at  timestamptz NOT NULL DEFAULT now(),
    paid_at     timestamptz
);
CREATE INDEX IF NOT EXISTS payments_order_idx ON payments (order_id);


-- ------------------------------------------------------------
-- Подсказка каталожного номера
-- ------------------------------------------------------------
-- Номер вводится руками, и это самое узкое место приёмки: ошибка
-- в номере — это возврат. Ядро подсказки собирает кандидатов из
-- нескольких источников и решает, можно ли подставить номер сам.

-- Откуда взялся номер и подтверждал ли его человек. Номер, который
-- подставил автомат и никто не сверил с деталью, не должен уезжать
-- в выгрузку на площадки: ошибиться внутри склада дёшево, в объявлении —
-- нет.
ALTER TABLE parts ADD COLUMN IF NOT EXISTS oem_source text;
ALTER TABLE parts ADD COLUMN IF NOT EXISTS oem_verified boolean NOT NULL DEFAULT false;

-- Уже заведённое вводили руками, глядя на деталь
UPDATE parts SET oem_verified = true, oem_source = 'manual'
 WHERE oem_number IS NOT NULL AND oem_source IS NULL;

-- Что предложил каждый источник и что в итоге выбрали.
-- Это разметка для самокалибровки: через пару сотен деталей видно,
-- какой источник врёт, и веса можно считать, а не задавать на глаз.
CREATE TABLE IF NOT EXISTS part_number_candidates (
    id         bigserial PRIMARY KEY,
    part_id    bigint NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    code       text   NOT NULL,
    source     text   NOT NULL,
    weight     numeric(6,2) NOT NULL DEFAULT 1,
    chosen     boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS part_number_candidates_part_idx
    ON part_number_candidates (part_id);
CREATE INDEX IF NOT EXISTS part_number_candidates_source_idx
    ON part_number_candidates (source, chosen);

-- Подсказка ищет «такой же узел на такой же машине» — это главный
-- и самый дешёвый источник, но по нему не было индекса
CREATE INDEX IF NOT EXISTS parts_oem_lookup_idx
    ON parts (category_id, oem_number) WHERE oem_number IS NOT NULL;


-- ------------------------------------------------------------
-- Происхождение детали: оригинал / ОЕМ / аналог
-- ------------------------------------------------------------
-- Это не украшение карточки, а условие для поиска номера. У трёх типов
-- номера принадлежат разным производителям:
--   original    — номер автозавода (8450039385 у АвтоВАЗа)
--   oem         — деталь того же поставщика, что шёл на конвейер,
--                 но под его брендом и его номером (Bosch, Hella, Valeo)
--   aftermarket — неоригинальный заменитель, номер бренда-изготовителя
-- Снятая с машины деталь обычно оригинал, но не всегда: до нас её мог
-- кто-то заменить аналогом, и тогда на ней чужой номер.
ALTER TABLE parts ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'original';

DO $$
BEGIN
    ALTER TABLE parts ADD CONSTRAINT parts_origin_check
        CHECK (origin IN ('original', 'oem', 'aftermarket'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Бренд детали: у оригинала это марка машины, у ОЕМ и аналога — свой
-- производитель. Без него номер ОЕМ не с чем сверять
ALTER TABLE parts ADD COLUMN IF NOT EXISTS part_brand text;

CREATE INDEX IF NOT EXISTS parts_origin_idx ON parts (origin);


-- ------------------------------------------------------------
-- Кеш внешних запросов
-- ------------------------------------------------------------
-- Парсер ходит наружу, а внешние источники нестабильны и не любят
-- частоты. Один и тот же вопрос задаём один раз: ответ живёт в кеше,
-- в том числе отрицательный — «ничего не нашлось» тоже результат,
-- и переспрашивать его каждый раз бессмысленно.
CREATE TABLE IF NOT EXISTS external_lookups (
    id         bigserial PRIMARY KEY,
    source     text NOT NULL,
    query      text NOT NULL,
    payload    jsonb,                       -- что вернул источник
    found      int  NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, query)
);
CREATE INDEX IF NOT EXISTS external_lookups_age_idx ON external_lookups (created_at);
