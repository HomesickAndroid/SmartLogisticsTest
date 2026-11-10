CREATE SCHEMA IF NOT EXISTS mart;

-- Первичный ключ задаёт состав аналитической ячейки и запрещает её дублирование.
CREATE TABLE IF NOT EXISTS mart.market_corridor (
    month date NOT NULL,
    loading_city_gcid integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    body_group text NOT NULL,
    weight_category text NOT NULL,
    origin_city text NOT NULL,
    destination_city text NOT NULL,
    p25_trip double precision NOT NULL,
    median_trip double precision NOT NULL,
    p75_trip double precision NOT NULL,
    avg_trip double precision NOT NULL,
    p25_km double precision NOT NULL,
    median_km double precision NOT NULL,
    p75_km double precision NOT NULL,
    trips_count bigint NOT NULL,
    carriers_count bigint NOT NULL,
    cv_rate double precision,
    mom_pct double precision,
    yoy_pct double precision,
    last_observation_date date NOT NULL,
    reliability_level text NOT NULL,
    as_of date NOT NULL,
    PRIMARY KEY(month, loading_city_gcid, unloading_city_gcid, body_group, weight_category)
);
CREATE INDEX IF NOT EXISTS market_corridor_route_idx ON mart.market_corridor
    (loading_city_gcid, unloading_city_gcid, body_group, weight_category, month);
CREATE OR REPLACE VIEW mart.mart_route_card AS SELECT * FROM mart.market_corridor;

-- Реестр хранит параметры, качество и путь к файлу каждой версии модели.
CREATE TABLE IF NOT EXISTS mart.ml_models (
    model_name text PRIMARY KEY,
    as_of date NOT NULL,
    train_start date NOT NULL,
    test_start date NOT NULL,
    train_count integer NOT NULL,
    test_count integer NOT NULL,
    metrics jsonb NOT NULL,
    features jsonb NOT NULL,
    parameters jsonb NOT NULL,
    data_fingerprint text NOT NULL,
    artifact_path text NOT NULL,
    trained_at timestamptz NOT NULL DEFAULT now()
);
-- На одну заявку и дату снимка допускается один прогноз. У будущих заявок факт и ошибки пусты.
CREATE TABLE IF NOT EXISTS mart.ml_price_predictions (
    price_id bigint NOT NULL,
    as_of date NOT NULL,
    loading_date date NOT NULL,
    loading_city_gcid integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    origin_city text NOT NULL,
    destination_city text NOT NULL,
    body_group text NOT NULL,
    weight_category text NOT NULL,
    prediction_kind text NOT NULL CHECK (prediction_kind IN ('test','future')),
    actual_price_rub double precision,
    predicted_price_rub double precision NOT NULL CHECK (predicted_price_rub > 0),
    abs_error_rub double precision,
    ape_pct double precision,
    model_name text NOT NULL REFERENCES mart.ml_models,
    scored_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (as_of, price_id),
    CHECK (prediction_kind <> 'future' OR
           (actual_price_rub IS NULL AND abs_error_rub IS NULL AND ape_pct IS NULL))
);
CREATE INDEX IF NOT EXISTS predictions_date_idx ON mart.ml_price_predictions(loading_date);
-- Только для отчётного экрана разворачиваем список кузовов в отдельные строки.
-- Заявка доступна под каждой совместимой группой; основная таблица прогнозов не меняется.
CREATE OR REPLACE VIEW mart.ml_predictions_by_body AS
SELECT p.price_id, p.as_of, p.loading_date, p.loading_city_gcid, p.unloading_city_gcid,
       p.origin_city, p.destination_city, b.body_group, p.body_group AS body_groups,
       p.weight_category, p.prediction_kind, p.actual_price_rub, p.predicted_price_rub,
       p.abs_error_rub, p.ape_pct, p.model_name, p.scored_at
FROM mart.ml_price_predictions p
CROSS JOIN LATERAL string_to_table(p.body_group, ' | ') AS b(body_group);
CREATE OR REPLACE VIEW mart.ml_quality AS
SELECT model_name, as_of, train_start, test_start, train_count, test_count,
       (metrics->>'r2')::double precision AS r2,
       (metrics->>'mape_pct')::double precision AS mape_pct,
       (metrics->>'rmse_rub')::double precision AS rmse_rub,
       (metrics->>'mae_rub')::double precision AS mae_rub, trained_at
FROM mart.ml_models;
