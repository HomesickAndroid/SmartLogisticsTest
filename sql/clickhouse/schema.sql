-- Полные копии двух витрин PostgreSQL. Порядок столбцов совпадает с sql/mart/schema.sql.
CREATE DATABASE IF NOT EXISTS mart ENGINE = Atomic;

CREATE TABLE IF NOT EXISTS mart.market_corridor (
    month Date,
    loading_city_gcid Int32,
    unloading_city_gcid Int32,
    body_group String,
    weight_category String,
    origin_city String,
    destination_city String,
    p25_trip Float64,
    median_trip Float64,
    p75_trip Float64,
    avg_trip Float64,
    p25_km Float64,
    median_km Float64,
    p75_km Float64,
    trips_count Int64,
    carriers_count Int64,
    cv_rate Nullable(Float64),
    mom_pct Nullable(Float64),
    yoy_pct Nullable(Float64),
    last_observation_date Date,
    reliability_level String,
    as_of Date
) ENGINE = MergeTree
PARTITION BY toYYYYMM(month)
ORDER BY (month, loading_city_gcid, unloading_city_gcid, body_group, weight_category);

CREATE TABLE IF NOT EXISTS mart.ml_price_predictions (
    price_id Int64,
    as_of Date,
    loading_date Date,
    loading_city_gcid Int32,
    unloading_city_gcid Int32,
    origin_city String,
    destination_city String,
    body_group String,
    weight_category String,
    prediction_kind String,
    actual_price_rub Nullable(Float64),
    predicted_price_rub Float64,
    abs_error_rub Nullable(Float64),
    ape_pct Nullable(Float64),
    model_name String,
    scored_at DateTime64(6, 'UTC')
) ENGINE = MergeTree
PARTITION BY toYYYYMM(loading_date)
ORDER BY (as_of, loading_date, price_id);
