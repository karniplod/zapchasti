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


-- ------------------------------------------------------------
-- Объединение дублей справочника
-- ------------------------------------------------------------
-- Странице проверки нужен model_id: сливать поколение можно только
-- внутри своей модели, иначе применимость снятых деталей разъедется.
-- Во вью его не было, потому что до появления интерфейса объединение
-- вызывали руками, зная идентификаторы.
--
-- Колонка дописана в конец: CREATE OR REPLACE не позволяет менять
-- имена и порядок существующих, а вставка в середину читается именно
-- как переименование.
CREATE OR REPLACE VIEW reference_review AS
SELECT g.id,
       b.name AS brand, m.name AS model, g.name AS generation,
       g.body_type, g.year_from, g.year_to, g.source,
       (SELECT count(*) FROM donors d WHERE d.generation_id = g.id) AS donors,
       g.model_id
  FROM generations g
  JOIN models m ON m.id = g.model_id
  JOIN brands b ON b.id = m.brand_id
 WHERE g.needs_review
 ORDER BY donors DESC, b.name, m.name;


-- ------------------------------------------------------------
-- «Сверен» — только номер, набранный с детали
-- ------------------------------------------------------------
-- Раньше сверенным считался любой сохранённый номер, в том числе
-- принятый из подсказки. Своя история учитывает только сверенные,
-- и номер из веб-поиска, сохранённый один раз, возвращался в подсказку
-- «своей историей» — то есть подтверждал сам себя вторым голосом.
-- С автоподстановкой это стало бы самоусиливающимся: подставленный
-- номер сохраняется, потом подставляется увереннее, и так по кругу.
--
-- Номера с неизвестным источником (NULL — заведены до появления
-- колонки) не трогаем: про них нельзя сказать, откуда они.
-- Проверенный менеджером номер получает oem_source = 'manual',
-- поэтому повторный прогон его не снимает.
UPDATE parts SET oem_verified = false
 WHERE oem_verified
   AND oem_source IS NOT NULL
   AND oem_source <> 'manual';


-- ------------------------------------------------------------
-- Вопрос по машине со страницы /cars/{code}
-- ------------------------------------------------------------
-- «Что можно снять под заказ» — заявка не про деталь, а про машину.
-- Менеджеру в бэкенде нужно видеть, о какой речь. Машину удалили —
-- заявка остаётся: по ней видно, о чём спрашивали.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS donor_id int
    REFERENCES donors(id) ON DELETE SET NULL;


-- ------------------------------------------------------------
-- Описание машины для покупателя
-- ------------------------------------------------------------
-- notes — внутренние заметки приёмщика: там бывает и цена торга,
-- и что угодно ещё, наружу их не отдаём. Покупателю полезно другое —
-- «удар в заднюю часть, передок целый». Отдельное поле, которое
-- приёмщик пишет, зная, что его увидят на сайте (/cars/{code}).
ALTER TABLE donors ADD COLUMN IF NOT EXISTS public_note text;


-- ------------------------------------------------------------
-- Повтор отправки детали из приложения
-- ------------------------------------------------------------
-- Wifi в цеху рвётся: запрос с деталью дошёл, ответ потерялся, телефон
-- отправляет её ещё раз. Без ключа это вторая деталь с новым артикулом
-- и теми же фото. Ключ придумывает телефон, сервер по нему узнаёт уже
-- созданную. С сайта ключа нет — там NULL, на уникальность не влияет.
ALTER TABLE parts ADD COLUMN IF NOT EXISTS client_key uuid;
CREATE UNIQUE INDEX IF NOT EXISTS parts_client_key_uniq
    ON parts (client_key) WHERE client_key IS NOT NULL;


-- ------------------------------------------------------------
-- Отчёты по запросам покупателей
-- ------------------------------------------------------------
-- Поиск по названию писался только у вошедших — для истории в
-- кабинете. Для отчёта нужен весь спрос, поэтому теперь пишутся и
-- анонимные (customer_id NULL), с городом, который был выбран.
ALTER TABLE search_queries ADD COLUMN IF NOT EXISTS city text;
CREATE INDEX IF NOT EXISTS search_queries_created_idx
    ON search_queries (created_at DESC);

-- Подбор через каталог: какой узел открыли, какие состояния отметили,
-- цена, машина, город — и сколько нашлось. Запись ставит фронт, когда
-- выбор устоялся (полторы секунды без изменений), а не на каждый щелчок.
CREATE TABLE IF NOT EXISTS catalog_browses (
    id            bigserial PRIMARY KEY,
    customer_id   bigint REFERENCES customers(id) ON DELETE SET NULL,
    category_id   int REFERENCES part_categories(id) ON DELETE SET NULL,
    generation_id int REFERENCES generations(id) ON DELETE SET NULL,
    conditions    text[],
    price_min     int,
    price_max     int,
    city          text,
    results_count int NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS catalog_browses_created_idx
    ON catalog_browses (created_at DESC);


-- ------------------------------------------------------------
-- Новые города: Владивосток и Самара, по одному филиалу
-- ------------------------------------------------------------
-- Город в каталоге и шапке берётся из филиалов, так что отдельно
-- заводить его нигде не нужно.
INSERT INTO branches (city, name, sort_order) VALUES
    ('Владивосток', 'Сибирская 7', 5),
    ('Самара',      'Южная 5',     6)
ON CONFLICT (city, name) DO NOTHING;


-- ------------------------------------------------------------
-- Вход покупателя: телефон или email, быстрый вход через соцсети
-- ------------------------------------------------------------
-- Кабинет теперь заводится и по email, и через Google, VK, MAX,
-- Telegram — у такого покупателя телефона может не быть. Телефон
-- спрашивается при оформлении заказа, если его ещё нет.
ALTER TABLE customers ALTER COLUMN phone DROP NOT NULL;

-- Email — второй логин, значит, уникален без учёта регистра. Индекс
-- ставим, только если дублей нет: иначе миграция упала бы целиком
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM customers WHERE email IS NOT NULL
                    GROUP BY lower(email) HAVING count(*) > 1) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS customers_email_uniq
            ON customers (lower(email)) WHERE email IS NOT NULL;
    END IF;
END $$;

-- Учётка у провайдера → покупатель. Один человек может войти и через
-- Google, и через Telegram — это две строки на одного покупателя
CREATE TABLE IF NOT EXISTS customer_identities (
    provider    text   NOT NULL,          -- google / vk / max / telegram
    subject     text   NOT NULL,          -- id пользователя у провайдера
    customer_id bigint NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    display     text,                     -- имя или ник — показать в кабинете
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, subject)
);
CREATE INDEX IF NOT EXISTS customer_identities_customer_idx
    ON customer_identities (customer_id);

-- Вход через MAX: у мессенджера нет OAuth, вход идёт через бота.
-- Сайт выдаёт одноразовый код, человек открывает бота по ссылке
-- с этим кодом, бот присылает вебхук — строка получает пользователя,
-- а страница ожидания забирает вход. Код живёт пять минут
CREATE TABLE IF NOT EXISTS max_logins (
    token        text PRIMARY KEY,
    max_user_id  bigint,
    max_name     text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    confirmed_at timestamptz,
    used_at      timestamptz
);


-- ------------------------------------------------------------
-- Оформление заказа: получатель, пункт выдачи, способ оплаты
-- ------------------------------------------------------------
ALTER TABLE orders ADD COLUMN IF NOT EXISTS contact_name  text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS contact_phone text;
-- Самовывоз — из какого филиала; доставка — адрес в delivery_address
ALTER TABLE orders ADD COLUMN IF NOT EXISTS pickup_branch_id int
    REFERENCES branches(id) ON DELETE SET NULL;
-- online — картой или СБП на сайте, on_receipt — при получении
ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_method text;

-- Ссылка на страницу банка: вернулся человек со страницы заказа —
-- продолжает ту же оплату, а не заводит вторую
ALTER TABLE payments ADD COLUMN IF NOT EXISTS confirmation_url text;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS provider text;
CREATE UNIQUE INDEX IF NOT EXISTS payments_external_uniq
    ON payments (provider, external_id) WHERE external_id IS NOT NULL;


-- ------------------------------------------------------------
-- Подтверждение email письмом
-- ------------------------------------------------------------
-- Регистрация по email требует перейти по ссылке из письма: без этого
-- кабинет можно завести на чужой адрес. Телефон и вход через соцсети
-- не затронуты. Время отправки — чтобы не слать письмо чаще раза в минуту.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS email_verified_at timestamptz;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS email_verify_sent_at timestamptz;


-- ------------------------------------------------------------
-- Количество штук у детали
-- ------------------------------------------------------------
-- Раньше деталь была штучной: одна строка — одна вещь. Теперь у строки
-- есть остаток: четыре одинаковых диска с одной машины — одна карточка
-- «4 шт». quantity — сколько штук свободно на складе: заказ списывает
-- заказанное, отмена возвращает. Ноль — деталь уходит с витрины
-- (статус reserved), как раньше уходила единственная штука.
-- Колонку заводим и заполняем в одном блоке, один раз: занятые и
-- проданные детали получают остаток 0 — их штука уже у покупателя.
-- Иначе отмена старого заказа вернула бы на склад «вторую» штуку.
-- Повторный прогон миграций сюда не заходит: колонка уже есть
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'parts' AND column_name = 'quantity') THEN
        ALTER TABLE parts ADD COLUMN quantity int NOT NULL DEFAULT 1
            CONSTRAINT parts_quantity_check CHECK (quantity >= 0);
        UPDATE parts SET quantity = 0
         WHERE status IN ('reserved', 'sold', 'written_off');
    END IF;
END $$;

-- Сколько штук положили в корзину и сколько купили. Цена в order_items —
-- за штуку, сумма строки — price * qty
ALTER TABLE cart_items  ADD COLUMN IF NOT EXISTS qty int NOT NULL DEFAULT 1;
ALTER TABLE order_items ADD COLUMN IF NOT EXISTS qty int NOT NULL DEFAULT 1;


-- ------------------------------------------------------------
-- Доставка службами: СДЭК, Яндекс Доставка, Почта России
-- ------------------------------------------------------------
-- Для расчёта службам нужны вес и габариты. Вес у детали есть (weight_kg),
-- габариты вводить в сантиметрах никто не станет — размер выбирают при
-- приёме: S мелкая, M средняя, L крупная, XL очень крупная; за каждым —
-- типовая коробка (app/delivery.py). Пусто — считаем средней
ALTER TABLE parts ADD COLUMN IF NOT EXISTS size_class text;

-- Откуда отправляем: индекс отделения для Почты, код города СДЭК
-- (подбирается сам по названию и запоминается)
ALTER TABLE branches ADD COLUMN IF NOT EXISTS postcode text;
ALTER TABLE branches ADD COLUMN IF NOT EXISTS cdek_city_code int;
UPDATE branches SET postcode = CASE city
        WHEN 'Пермь' THEN '614000' WHEN 'Москва' THEN '101000'
        WHEN 'Владивосток' THEN '690000' WHEN 'Самара' THEN '443000' END
 WHERE postcode IS NULL;

-- Что выбрал покупатель: служба, способ (пункт выдачи / до двери /
-- отделение), стоимость на момент заказа, пункт выдачи
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_carrier text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_mode text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_tariff text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_price numeric(12,2);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_days text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_city text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_point text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_point_address text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_postcode text;


-- ------------------------------------------------------------
-- Доставка посылками по филиалам
-- ------------------------------------------------------------
-- Детали заказа из разных филиалов едут отдельными посылками: каждая —
-- из своего города, своей ценой, со своим номером для отслеживания.
-- Служба и пункт выдачи у всех посылок заказа одни — их выбирает
-- покупатель один раз.

-- Склад Яндекс Доставки, заведённый в кабинете Яндекса для филиала:
-- без него Яндекс из этого филиала не возит
ALTER TABLE branches ADD COLUMN IF NOT EXISTS yandex_station_id text;

CREATE TABLE IF NOT EXISTS order_shipments (
    id            bigserial PRIMARY KEY,
    order_id      bigint NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    branch_id     int REFERENCES branches(id) ON DELETE SET NULL,
    carrier       text,                 -- cdek / yandex / pochta
    mode          text,                 -- pvz / door / post
    tariff        text,
    price         numeric(12,2),
    days_min      int,
    days_max      int,
    weight_g      int,
    point         text,                 -- код пункта выдачи
    address       text,                 -- пункт или адрес получателя
    track_number  text,
    -- assembling — собирается, sent — отправлена, delivered — доставлена,
    -- cancelled — заказ отменён
    status        text NOT NULL DEFAULT 'assembling',
    sent_at       timestamptz,
    delivered_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_shipments_order_idx ON order_shipments (order_id);
CREATE INDEX IF NOT EXISTS order_shipments_branch_idx ON order_shipments (branch_id, status);

ALTER TABLE order_items ADD COLUMN IF NOT EXISTS shipment_id bigint
    REFERENCES order_shipments(id) ON DELETE SET NULL;

-- Заказы с доставкой службой, оформленные до посылок, — одной посылкой
-- из филиала первой детали: так страницы заказа показывают их одинаково
DO $$
DECLARE o record; sid bigint;
BEGIN
    FOR o IN SELECT * FROM orders
              WHERE delivery_carrier IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM order_shipments s WHERE s.order_id = orders.id)
    LOOP
        INSERT INTO order_shipments (order_id, branch_id, carrier, mode, tariff, price,
                                     point, address, status)
        VALUES (o.id,
                (SELECT p.branch_id FROM order_items oi JOIN parts p ON p.id = oi.part_id
                  WHERE oi.order_id = o.id ORDER BY oi.id LIMIT 1),
                o.delivery_carrier, o.delivery_mode, o.delivery_tariff, o.delivery_price,
                o.delivery_point, coalesce(o.delivery_point_address, o.delivery_address),
                CASE WHEN o.status = 'cancelled' THEN 'cancelled'
                     WHEN o.status = 'completed' THEN 'delivered'
                     WHEN o.status = 'shipped' THEN 'sent' ELSE 'assembling' END)
        RETURNING id INTO sid;
        UPDATE order_items SET shipment_id = sid WHERE order_id = o.id;
    END LOOP;
END $$;


-- ------------------------------------------------------------
-- Карточка заказа в бэкенде: правка данных и лента событий
-- ------------------------------------------------------------
-- Покупатель ошибся в имени, телефоне или адресе — менеджер правит
-- заказ сам. Адрес храним и по полям: строку целиком не поправить
-- без риска сломать формат для службы доставки.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS contact_email      text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS manager_note       text;   -- видно только сотрудникам
ALTER TABLE orders ADD COLUMN IF NOT EXISTS updated_at         timestamptz;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_country   text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_street    text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_house     text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_block     text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_flat      text;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_cdek_code int;    -- город у СДЭК, для пересчёта

-- Лента заказа: что происходило и кто это сделал. user_id пуст —
-- сделал покупатель или сам сайт (оформление, онлайн-оплата)
CREATE TABLE IF NOT EXISTS order_events (
    id          bigserial PRIMARY KEY,
    order_id    bigint NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    user_id     int REFERENCES users(id) ON DELETE SET NULL,
    -- created, status, edit, item, shipment, delivery, payment, note
    kind        text NOT NULL,
    text        text NOT NULL,
    data        jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_events_order_idx ON order_events (order_id, created_at);
CREATE INDEX IF NOT EXISTS orders_contact_phone_idx ON orders (contact_phone);


-- ------------------------------------------------------------
-- Откуда деталь в заказе
-- ------------------------------------------------------------
-- cart — оформлена из корзины, customer — покупатель добавил в кабинете,
-- manager — добавил менеджер. Видно и покупателю, и в карточке заказа
ALTER TABLE order_items ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'cart';

-- Добавленные раньше — по ленте заказа: там записано, кто что добавил
UPDATE order_items oi SET source = 'customer'
 WHERE oi.source = 'cart' AND EXISTS (
       SELECT 1 FROM order_events e JOIN parts p ON p.id = oi.part_id
        WHERE e.order_id = oi.order_id AND e.kind = 'edit'
          AND e.text LIKE 'Покупатель изменил заказ:%Добавлено: ' || p.sku || '%');
UPDATE order_items oi SET source = 'manager'
 WHERE oi.source = 'cart' AND EXISTS (
       SELECT 1 FROM order_events e JOIN parts p ON p.id = oi.part_id
        WHERE e.order_id = oi.order_id AND e.kind = 'item'
          AND e.text LIKE 'Добавлено: ' || p.sku || '%');
