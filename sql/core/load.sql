-- Берём ID и из источника, и из ранее загруженного периода.
-- Так перенос даты или удаление исходной сделки не оставит устаревшую запись в хранилище.
CREATE TEMP TABLE batch_ids ON COMMIT DROP AS
SELECT id AS price_id FROM raw.prices
WHERE (%(start)s::date IS NULL OR loading_date >= %(start)s)
  AND (%(end)s::date IS NULL OR loading_date < %(end)s)
UNION
SELECT price_id FROM core.shipments
WHERE (%(start)s::date IS NULL OR loading_date >= %(start)s)
  AND (%(end)s::date IS NULL OR loading_date < %(end)s)
UNION
SELECT price_id FROM core.dq_quarantine_prices
WHERE (%(start)s::date IS NULL OR loading_date >= %(start)s)
  AND (%(end)s::date IS NULL OR loading_date < %(end)s);
CREATE UNIQUE INDEX ON batch_ids(price_id);

-- Из дублей города выбираем строку с наименьшим id; регион извлекаем после первой запятой.
INSERT INTO core.cities
SELECT DISTINCT ON (gc_id) gc_id, id, trim(split_part(full_name_local, ',', 1)),
       CASE WHEN strpos(full_name_local, ',') > 0
            THEN trim(substr(full_name_local, strpos(full_name_local, ',')+1))
            ELSE 'Не указан' END, latitude, longitude
FROM raw.cities ORDER BY gc_id, id
ON CONFLICT (gc_id) DO UPDATE SET source_id=EXCLUDED.source_id, city=EXCLUDED.city,
    region=EXCLUDED.region, latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude;
DELETE FROM core.cities c WHERE NOT EXISTS (SELECT 1 FROM raw.cities r WHERE r.gc_id=c.gc_id);

-- Готовим временный набор: сохраняем исходную строку, восстанавливаем категории,
-- отдельно отмечаем ошибки параметров перевозки и ошибки цены/курса.
CREATE TEMP TABLE batch_source ON COMMIT DROP AS
SELECT p.*, to_jsonb(p) AS source_row,
    CASE WHEN currency_code='643' THEN 1.0 ELSE fx.value END AS fx_rate,
    coalesce(nullif(p.weight_category,''), CASE
        WHEN round(total_weight::numeric,1) >= 30 THEN 'G20T'
        WHEN round(total_weight::numeric,1) >= 10 THEN 'FTL'
        WHEN round(total_weight::numeric,1) >= 5 THEN '10T'
        WHEN round(total_weight::numeric,1) >= 2.5 THEN '5T'
        WHEN round(total_weight::numeric,1) >= 0.5 THEN 'L5T' ELSE 'OTH' END) AS clean_weight,
    coalesce(nullif(p.volume_category,''), CASE WHEN total_volume > 0 AND total_volume <= 120
        THEN 'FTL' ELSE 'OTH' END) AS clean_volume,
    array_remove(ARRAY[
        CASE WHEN NOT (distance > 0 AND distance < 'Infinity'::float8) THEN 'INVALID_DISTANCE' END,
        CASE WHEN NOT (total_weight >= 0 AND total_weight < 'Infinity'::float8) THEN 'INVALID_WEIGHT' END,
        CASE WHEN NOT (total_volume >= 0 AND total_volume < 'Infinity'::float8) THEN 'INVALID_VOLUME' END,
        CASE WHEN lc.gc_id IS NULL THEN 'UNKNOWN_ORIGIN' END,
        CASE WHEN uc.gc_id IS NULL THEN 'UNKNOWN_DESTINATION' END
    ], NULL) AS physical_reasons,
    array_remove(ARRAY[
        CASE WHEN NOT (price > 0 AND price < 'Infinity'::float8) THEN 'INVALID_PRICE' END,
        CASE WHEN currency_code='643' AND price > 0 AND price <= 1000 THEN 'RUB_PRICE_LE_1000' END,
        CASE WHEN currency_code NOT IN ('643','840','978') THEN 'UNSUPPORTED_CURRENCY' END,
        CASE WHEN currency_code IN ('840','978') AND
            (fx.value IS NULL OR NOT (fx.value > 0 AND fx.value < 'Infinity'::float8))
            THEN 'MISSING_EXACT_FX_RATE' END
    ], NULL) AS monetary_reasons
FROM raw.prices p JOIN batch_ids i ON i.price_id=p.id
LEFT JOIN core.cities lc ON lc.gc_id=p.loading_city_gcid
LEFT JOIN core.cities uc ON uc.gc_id=p.unloading_city_gcid
-- Подходит только курс на сохранённую nearest_rate_date. По дате погрузки курс не ищем.
LEFT JOIN LATERAL (SELECT value FROM raw.exchange_rates er
                  WHERE er.num_code=p.currency_code AND er.setting_date=p.nearest_rate_date
                  ORDER BY er.id LIMIT 1) fx ON p.currency_code IN ('840','978');

-- Заменяем выбранные сделки целиком в транзакции. Связанные цены, кузова и даты
-- удаляются по внешним ключам и ниже создаются заново без накопления дублей.
DELETE FROM core.shipments s USING batch_ids b WHERE s.price_id=b.price_id;
DELETE FROM core.dq_quarantine_prices q USING batch_ids b WHERE q.price_id=b.price_id;
-- Параметры корректной перевозки сохраняем даже при плохой цене: они нужны для будущего прогноза.
INSERT INTO core.shipments
SELECT id, loading_date, loading_city_gcid, unloading_city_gcid, distance, total_weight,
       total_volume, clean_weight, clean_volume, organization_id, price_type,
       is_fact, complex, outlier_mark
FROM batch_source WHERE cardinality(physical_reasons)=0;
-- Нормализованную цену получают только строки без всех перечисленных ошибок.
INSERT INTO core.prices
SELECT id, price, currency_code, nearest_rate_date, fx_rate,
       price::numeric * fx_rate::numeric
FROM batch_source WHERE cardinality(physical_reasons || monetary_reasons)=0;
-- В карантине остаётся исходная запись и полный список причин отсева.
INSERT INTO core.dq_quarantine_prices
SELECT id, loading_date, physical_reasons || monetary_reasons, source_row
FROM batch_source WHERE cardinality(physical_reasons || monetary_reasons)>0;

-- Связи кузовов имеют приоритет над одиночным полем сделки; DISTINCT убирает повторы группы.
INSERT INTO core.price_body_groups
SELECT DISTINCT s.price_id, core.body_group(coalesce(bt.std_id, coalesce(pb.body_type_id,p.body_type)))
FROM core.shipments s JOIN batch_source p ON p.id=s.price_id
LEFT JOIN raw.price_body_types pb ON pb.price_id=s.price_id
LEFT JOIN raw.body_types bt ON bt.id=coalesce(pb.body_type_id,p.body_type);
INSERT INTO core.price_dates
SELECT DISTINCT s.price_id, d.price_date
FROM core.shipments s JOIN batch_ids b USING(price_id) JOIN raw.price_dates d USING(price_id);
