-- ops хранит сведения о загрузках, core — очищенные данные, mart — готовые показатели.
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS ops.source_version (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    fingerprint text NOT NULL,
    n_prices integer NOT NULL,
    initialized_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops.runs (
    run_id uuid PRIMARY KEY,
    stage text NOT NULL,
    as_of date NOT NULL,
    start_date date,
    end_date date,
    status text NOT NULL DEFAULT 'running',
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    details jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS core.cities (
    gc_id integer PRIMARY KEY,
    source_id bigint NOT NULL,
    city text NOT NULL,
    region text NOT NULL,
    latitude double precision,
    longitude double precision
);
-- Перевозка и её цена разделены: будущую заявку можно оценить даже без корректной сырой цены.
CREATE TABLE IF NOT EXISTS core.shipments (
    price_id bigint PRIMARY KEY,
    loading_date date NOT NULL,
    loading_city_gcid integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    distance double precision NOT NULL CHECK (distance > 0),
    total_weight double precision NOT NULL CHECK (total_weight >= 0),
    total_volume double precision NOT NULL CHECK (total_volume >= 0),
    weight_category text NOT NULL,
    volume_category text NOT NULL,
    organization_id integer,
    price_type smallint NOT NULL,
    is_fact boolean NOT NULL,
    complex boolean NOT NULL,
    outlier_mark boolean NOT NULL
);
CREATE INDEX IF NOT EXISTS shipments_loading_date_idx ON core.shipments(loading_date);
CREATE TABLE IF NOT EXISTS core.prices (
    price_id bigint PRIMARY KEY REFERENCES core.shipments ON DELETE CASCADE,
    source_price double precision NOT NULL,
    currency_code varchar(3) NOT NULL,
    nearest_rate_date date,
    fx_rate double precision NOT NULL,
    price_rub numeric(24,6) NOT NULL CHECK (price_rub > 0)
);
CREATE TABLE IF NOT EXISTS core.price_body_groups (
    price_id bigint REFERENCES core.shipments ON DELETE CASCADE,
    body_group text NOT NULL,
    PRIMARY KEY (price_id, body_group)
);
CREATE TABLE IF NOT EXISTS core.price_dates (
    price_id bigint REFERENCES core.shipments ON DELETE CASCADE,
    price_date date NOT NULL,
    PRIMARY KEY (price_id, price_date)
);
CREATE TABLE IF NOT EXISTS core.dq_quarantine_prices (
    price_id bigint PRIMARY KEY,
    loading_date date NOT NULL,
    reasons text[] NOT NULL,
    source_row jsonb NOT NULL,
    CHECK (cardinality(reasons) > 0)
);
CREATE INDEX IF NOT EXISTS quarantine_loading_date_idx
    ON core.dq_quarantine_prices(loading_date);

-- Приводим коды кузовов к шести понятным бизнес-группам, включая отсутствие кузова.
CREATE OR REPLACE FUNCTION core.body_group(code integer) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN code IS NULL THEN 'Не указан'
       WHEN code IN (56,57,58,59,60,61) THEN 'Тент'
       WHEN code IN (4,46,47,48) THEN 'Рефрижератор'
       WHEN code = 2 THEN 'Изотерм'
       WHEN code IN (1,15) THEN 'Борт' ELSE 'Прочие' END
$$;

-- Для модели оставляем одну строку на сделку: совместимые кузова собираем в отсортированный список.
CREATE OR REPLACE VIEW core.ml_input AS
SELECT s.*, p.price_rub,
       lc.city AS origin_city, lc.region AS origin_region,
       lc.latitude AS origin_latitude, lc.longitude AS origin_longitude,
       uc.city AS destination_city, uc.region AS destination_region,
       uc.latitude AS destination_latitude, uc.longitude AS destination_longitude,
       b.body_group
FROM core.shipments s
LEFT JOIN core.prices p USING (price_id)
JOIN core.cities lc ON lc.gc_id = s.loading_city_gcid
JOIN core.cities uc ON uc.gc_id = s.unloading_city_gcid
JOIN (SELECT price_id, string_agg(body_group, ' | ' ORDER BY body_group) AS body_group
      FROM core.price_body_groups GROUP BY price_id) b USING (price_id);

