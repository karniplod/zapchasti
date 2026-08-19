-- ============================================================
-- Авторазбор: схема БД (PostgreSQL 14+)
-- Ядро: справочник авто -> донор (VIN) -> запчасть -> заказ
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- поиск по названию/OEM

-- ------------------------------------------------------------
-- 1. Справочник автомобилей
-- ------------------------------------------------------------

CREATE TABLE brands (
    id          serial PRIMARY KEY,
    name        text NOT NULL UNIQUE,            -- Toyota
    slug        text NOT NULL UNIQUE,            -- toyota
    logo_path   text
);

CREATE TABLE models (
    id          serial PRIMARY KEY,
    brand_id    int NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    name        text NOT NULL,                   -- Camry
    slug        text NOT NULL,
    UNIQUE (brand_id, slug)
);

-- Поколение = кузов + годы выпуска. Именно по нему клиент фильтрует.
CREATE TABLE generations (
    id          serial PRIMARY KEY,
    model_id    int NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    name        text NOT NULL,                   -- XV70
    body_type   text,                            -- седан / универсал / хэтчбек
    year_from   smallint NOT NULL,
    year_to     smallint,                        -- NULL = выпускается
    UNIQUE (model_id, name)
);

-- Модификация = двигатель + КПП + привод. Нужна для точной применимости.
CREATE TABLE modifications (
    id              serial PRIMARY KEY,
    generation_id   int NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    engine_code     text,                        -- 2AR-FE
    engine_volume   numeric(3,1),                -- 2.5
    fuel            text,                        -- бензин / дизель / гибрид
    power_hp        smallint,
    transmission    text,                        -- AT / MT / CVT
    drive           text                         -- FWD / RWD / AWD
);

-- ------------------------------------------------------------
-- 2. Доноры (принятые на разбор авто)
-- ------------------------------------------------------------

CREATE TYPE donor_status AS ENUM ('accepted', 'dismantling', 'dismantled', 'scrapped');

CREATE TABLE donors (
    id              serial PRIMARY KEY,
    code            text NOT NULL UNIQUE,        -- внутренний номер: D-0042
    vin             char(17) UNIQUE,             -- NULL если VIN утрачен
    modification_id int REFERENCES modifications(id),
    generation_id   int NOT NULL REFERENCES generations(id),
    year            smallint,
    color           text,
    mileage_km      int,
    plate           text,
    status          donor_status NOT NULL DEFAULT 'accepted',
    accepted_at     date NOT NULL DEFAULT CURRENT_DATE,
    purchase_price  numeric(12,2),               -- себестоимость машины
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON donors (vin);
CREATE INDEX ON donors (generation_id);

-- Фото машины при приёмке (акт осмотра)
CREATE TABLE donor_photos (
    id          serial PRIMARY KEY,
    donor_id    int NOT NULL REFERENCES donors(id) ON DELETE CASCADE,
    path        text NOT NULL,
    sort_order  smallint NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------
-- 3. Категории запчастей (дерево)
-- ------------------------------------------------------------

CREATE TABLE part_categories (
    id          serial PRIMARY KEY,
    parent_id   int REFERENCES part_categories(id) ON DELETE RESTRICT,
    name        text NOT NULL,                   -- Дверь передняя левая
    slug        text NOT NULL UNIQUE,
    sort_order  smallint NOT NULL DEFAULT 0
);
-- Пример: Кузов -> Двери -> Дверь передняя левая

-- ------------------------------------------------------------
-- 4. Запчасти
-- ------------------------------------------------------------

-- Состояние: единая шкала, не свободный текст
CREATE TYPE part_condition AS ENUM ('A', 'B', 'C', 'D');
-- A — отличное, B — хорошее (следы эксплуатации),
-- C — рабочее с дефектами, D — под восстановление/на запчасти

CREATE TYPE part_status AS ENUM ('draft', 'in_stock', 'reserved', 'sold', 'written_off');

CREATE TABLE parts (
    id              bigserial PRIMARY KEY,
    sku             text NOT NULL UNIQUE,        -- D-0042-0137, он же в QR
    donor_id        int NOT NULL REFERENCES donors(id) ON DELETE RESTRICT,
    category_id     int NOT NULL REFERENCES part_categories(id),
    name            text NOT NULL,
    oem_number      text,                        -- каталожный номер, нормализованный
    condition       part_condition NOT NULL,
    condition_note  text,                        -- «скол на кромке 2 см»
    price           numeric(12,2),
    status          part_status NOT NULL DEFAULT 'draft',
    location        text,                        -- стеллаж/ячейка, можно позже
    weight_kg       numeric(8,2),                -- для расчёта доставки
    published       boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON parts (status) WHERE status = 'in_stock';
CREATE INDEX ON parts (category_id);
CREATE INDEX ON parts (donor_id);
CREATE INDEX ON parts (oem_number);
CREATE INDEX parts_name_trgm ON parts USING gin (name gin_trgm_ops);

CREATE TABLE part_photos (
    id          bigserial PRIMARY KEY,
    part_id     bigint NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    path        text NOT NULL,
    sort_order  smallint NOT NULL DEFAULT 0
);
CREATE INDEX ON part_photos (part_id);

-- ------------------------------------------------------------
-- 5. Применимость — ключевая таблица для фильтра
-- ------------------------------------------------------------
-- Привязана к OEM-номеру, а не к конкретной детали: заполняем один раз,
-- и все будущие детали с тем же номером сразу получают применимость.

CREATE TABLE oem_applicability (
    id              bigserial PRIMARY KEY,
    oem_number      text NOT NULL,
    generation_id   int NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    modification_id int REFERENCES modifications(id) ON DELETE CASCADE, -- NULL = все модификации
    UNIQUE (oem_number, generation_id, modification_id)
);
CREATE INDEX ON oem_applicability (oem_number);
CREATE INDEX ON oem_applicability (generation_id);

-- Ручная применимость для деталей без OEM-номера
CREATE TABLE part_applicability (
    part_id         bigint NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    generation_id   int NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    PRIMARY KEY (part_id, generation_id)
);

-- ------------------------------------------------------------
-- 6. Клиенты и заказы
-- ------------------------------------------------------------

CREATE TYPE order_status AS ENUM (
    'new', 'confirmed', 'paid', 'shipped', 'completed', 'cancelled'
);

CREATE TABLE customers (
    id          bigserial PRIMARY KEY,
    phone       text NOT NULL UNIQUE,
    email       text,
    name        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id              bigserial PRIMARY KEY,
    number          text NOT NULL UNIQUE,        -- 2026-000123
    customer_id     bigint REFERENCES customers(id),
    status          order_status NOT NULL DEFAULT 'new',
    source          text,                        -- site / avito / drom / phone
    total           numeric(12,2) NOT NULL DEFAULT 0,
    delivery_method text,
    delivery_address text,
    comment         text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    paid_at         timestamptz
);
CREATE INDEX ON orders (status);
CREATE INDEX ON orders (created_at DESC);

-- Цена фиксируется в момент заказа: деталь потом может подорожать
CREATE TABLE order_items (
    id          bigserial PRIMARY KEY,
    order_id    bigint NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    part_id     bigint NOT NULL REFERENCES parts(id) ON DELETE RESTRICT,
    price       numeric(12,2) NOT NULL,
    UNIQUE (order_id, part_id)   -- деталь штучная: не бывает qty > 1
);

-- ------------------------------------------------------------
-- 7. Заявки с витрины (кнопка «узнать наличие»)
-- ------------------------------------------------------------

CREATE TABLE leads (
    id          bigserial PRIMARY KEY,
    part_id     bigint REFERENCES parts(id) ON DELETE SET NULL,
    phone       text NOT NULL,
    name        text,
    message     text,
    processed   boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 8. Пользователи бэкенда
-- ------------------------------------------------------------

CREATE TYPE user_role AS ENUM ('admin', 'manager', 'dismantler');

CREATE TABLE users (
    id              serial PRIMARY KEY,
    login           text NOT NULL UNIQUE,
    password_hash   text NOT NULL,
    full_name       text,
    role            user_role NOT NULL DEFAULT 'manager',
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 9. Триггер updated_at
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER parts_updated_at BEFORE UPDATE ON parts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
