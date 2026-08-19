-- ============================================================
-- Дополнение к schema.sql: собственный VIN-декодер
-- ============================================================

-- Справочник WMI (позиции 1-3). Пополняется вручную по мере встречи
-- новых заводов. Один раз завёл — работает всегда.
CREATE TABLE wmi (
    code            char(3) PRIMARY KEY,
    brand_id        int REFERENCES brands(id),
    manufacturer    text NOT NULL,      -- "Hyundai Motor Manufacturing Rus"
    country         text,
    note            text
);

-- Накопленные паттерны VDS (позиции 4-8).
-- Ключ = WMI + VDS. Оператор подтвердил модификацию один раз —
-- следующая такая же машина определяется автоматически.
CREATE TABLE vin_patterns (
    id              serial PRIMARY KEY,
    wmi             char(3) NOT NULL,
    vds             char(5) NOT NULL,
    modification_id int NOT NULL REFERENCES modifications(id) ON DELETE CASCADE,
    hits            int NOT NULL DEFAULT 1,     -- сколько раз подтверждён
    created_by      int REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (wmi, vds, modification_id)
);
CREATE INDEX ON vin_patterns (wmi, vds);

-- Более грубый паттерн: только WMI + первые 2 символа VDS.
-- Срабатывает, когда точного совпадения нет — даёт хотя бы модель.
CREATE INDEX vin_patterns_short ON vin_patterns (wmi, substr(vds, 1, 2));

-- Сырой ответ декодера сохраняем у донора: пригодится при разборе спорных
-- случаев и при смене логики декодирования.
ALTER TABLE donors ADD COLUMN vin_decoded jsonb;
ALTER TABLE donors ADD COLUMN vin_source text;  -- 'pattern' / 'manual' / 'api'

-- Подбор модификации: сначала точное совпадение, потом грубое.
-- Чем больше hits, тем выше доверие.
CREATE OR REPLACE FUNCTION match_vin_pattern(p_wmi char(3), p_vds char(5))
RETURNS TABLE (modification_id int, confidence int) AS $$
    SELECT p.modification_id,
           CASE WHEN p.vds = p_vds THEN 100 ELSE 60 END - GREATEST(0, 10 - p.hits)
      FROM vin_patterns p
     WHERE p.wmi = p_wmi
       AND (p.vds = p_vds OR substr(p.vds, 1, 2) = substr(p_vds, 1, 2))
     ORDER BY (p.vds = p_vds) DESC, p.hits DESC
     LIMIT 1;
$$ LANGUAGE sql STABLE;

-- Запоминание паттерна после подтверждения оператором
CREATE OR REPLACE FUNCTION learn_vin_pattern(
    p_wmi char(3), p_vds char(5), p_mod int, p_user int
) RETURNS void AS $$
    INSERT INTO vin_patterns (wmi, vds, modification_id, created_by)
    VALUES (p_wmi, p_vds, p_mod, p_user)
    ON CONFLICT (wmi, vds, modification_id)
    DO UPDATE SET hits = vin_patterns.hits + 1;
$$ LANGUAGE sql;
