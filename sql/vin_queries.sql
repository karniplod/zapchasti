-- ============================================================
-- Дополнение: журнал VIN-запросов покупателей
-- ============================================================
-- Каждый VIN, который ввёл посетитель, — это заявка на спрос.
-- Неопознанные и «ничего не нашлось» показывают, какую машину
-- выгодно взять на разбор следующей.

CREATE TABLE vin_queries (
    id              bigserial PRIMARY KEY,
    vin             char(17) NOT NULL,
    wmi             char(3),
    vds             char(5),
    -- Чем закончился поиск:
    --   exact      — паттерн известен, поколение определено
    --   brand_year — узнали только марку и год, покупатель выбирал сам
    --   unknown    — WMI незнаком
    resolution      text NOT NULL,
    generation_id   int REFERENCES generations(id) ON DELETE SET NULL,
    results_count   int NOT NULL DEFAULT 0,   -- сколько деталей нашлось
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON vin_queries (created_at DESC);
CREATE INDEX ON vin_queries (resolution);
CREATE INDEX ON vin_queries (generation_id) WHERE generation_id IS NOT NULL;

-- Спрос, который вы не закрыли: искали, но ничего не нашлось.
-- Это и есть список машин на закупку.
CREATE OR REPLACE VIEW unmet_demand AS
SELECT q.wmi,
       w.manufacturer,
       q.generation_id,
       coalesce(b.name || ' ' || m.name || ' ' || g.name, 'не определено') AS car,
       count(*)                                   AS queries,
       count(DISTINCT q.vin)                      AS unique_vins,
       max(q.created_at)                          AS last_query
  FROM vin_queries q
  LEFT JOIN wmi w         ON w.code = q.wmi
  LEFT JOIN generations g ON g.id = q.generation_id
  LEFT JOIN models m      ON m.id = g.model_id
  LEFT JOIN brands b      ON b.id = m.brand_id
 WHERE q.results_count = 0
   AND q.created_at > now() - interval '180 days'
 GROUP BY q.wmi, w.manufacturer, q.generation_id, b.name, m.name, g.name
 ORDER BY unique_vins DESC;

-- Ускорение выдачи каталога
CREATE INDEX parts_catalog ON parts (category_id, condition, price)
    WHERE status = 'in_stock' AND published;
