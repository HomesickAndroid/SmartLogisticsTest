-- Собираем прошлые фактические перевозки без составных маршрутов и отмеченных выбросов.
-- Для одной сделки внутри месяца оставляем одну дату, чтобы не завышать число рейсов.
CREATE TEMP TABLE observations ON COMMIT DROP AS
SELECT s.price_id, d.month, d.last_date, s.loading_city_gcid, s.unloading_city_gcid,
       b.body_group, s.weight_category, s.organization_id, s.price_type,
       p.price_rub AS price_rub, p.price_rub::float8 / nullif(s.distance,0) AS rate_km
FROM core.shipments s JOIN core.prices p USING(price_id)
JOIN core.price_body_groups b USING(price_id)
JOIN (SELECT price_id, date_trunc('month',price_date)::date AS month, max(price_date) AS last_date
      FROM core.price_dates WHERE price_date <= %(as_of)s
      GROUP BY price_id, date_trunc('month',price_date)::date) d USING(price_id)
WHERE s.is_fact AND s.price_type IN (1,2,3,4,5,9) AND NOT s.complex
  AND NOT s.outlier_mark AND s.loading_date <= %(as_of)s AND p.price_rub > 0 AND s.distance > 0;

-- Одна строка расчёта: месяц × отправление × назначение × группа кузова × вес.
-- Квартили считаем по отдельным ценам, а не по уже готовым средним или медианам.
CREATE TEMP TABLE aggregates ON COMMIT DROP AS
SELECT month, loading_city_gcid, unloading_city_gcid, body_group, weight_category,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY price_rub) AS p25_trip,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY price_rub) AS median_trip,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY price_rub) AS p75_trip,
       avg(price_rub) AS avg_trip,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY rate_km) AS p25_km,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY rate_km) AS median_km,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY rate_km) AS p75_km,
       count(DISTINCT price_id) AS trips_count,
       count(DISTINCT organization_id) FILTER (WHERE price_type IN (3,9)) AS carriers_count,
       CASE WHEN count(*) >= 2 THEN stddev_samp(price_rub) / nullif(avg(price_rub),0) END AS cv_rate,
       max(last_date) AS last_observation_date
FROM observations GROUP BY month, loading_city_gcid, unloading_city_gcid, body_group, weight_category;
CREATE UNIQUE INDEX ON aggregates(month, loading_city_gcid, unloading_city_gcid, body_group, weight_category);
ANALYZE aggregates;

-- Всю витрину заменяем в транзакции: читатели увидят старый или новый полный результат.
DELETE FROM mart.market_corridor;
INSERT INTO mart.market_corridor
SELECT a.month, a.loading_city_gcid, a.unloading_city_gcid, a.body_group, a.weight_category,
       lc.city, uc.city, a.p25_trip, a.median_trip, a.p75_trip, a.avg_trip,
       a.p25_km, a.median_km, a.p75_km, a.trips_count, a.carriers_count, a.cv_rate,
       100 * (a.median_trip / nullif(m.median_trip,0) - 1) AS mom_pct,
       100 * (a.median_trip / nullif(y.median_trip,0) - 1) AS yoy_pct,
       a.last_observation_date,
       -- Высокая надёжность требует достаточного числа рейсов, перевозчиков и свежих данных.
       CASE WHEN a.trips_count >= 30 AND a.carriers_count >= 5
                 AND %(as_of)s::date - a.last_observation_date BETWEEN 0 AND 30 THEN 'HIGH'
            WHEN a.trips_count >= 10 THEN 'MEDIUM' ELSE 'LOW_DATA' END,
       %(as_of)s::date
FROM aggregates a
JOIN core.cities lc ON lc.gc_id=a.loading_city_gcid
JOIN core.cities uc ON uc.gc_id=a.unloading_city_gcid
-- Сравниваем только с точным предыдущим месяцем и тем же месяцем прошлого года.
-- Если календарной ячейки нет, изменение остаётся NULL, без поиска ближайшего периода.
LEFT JOIN aggregates m ON m.month=(a.month-interval '1 month')::date
 AND (m.loading_city_gcid,m.unloading_city_gcid,m.body_group,m.weight_category)=
     (a.loading_city_gcid,a.unloading_city_gcid,a.body_group,a.weight_category)
LEFT JOIN aggregates y ON y.month=(a.month-interval '1 year')::date
 AND (y.loading_city_gcid,y.unloading_city_gcid,y.body_group,y.weight_category)=
     (a.loading_city_gcid,a.unloading_city_gcid,a.body_group,a.weight_category);
