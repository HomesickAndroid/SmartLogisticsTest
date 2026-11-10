"""Создаёт подключения, наборы данных и отчётный экран Superset без повторных копий."""

import json
import os
from urllib.parse import quote

from superset import db
from superset.app import create_app


def metric(column, label=None, aggregate="MAX"):
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": column},
        "aggregate": aggregate,
        "label": label or column,
    }


# Настройки и внутренние таблицы Superset доступны только внутри созданного приложения.
app = create_app()
with app.app_context():
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.core import Database
    from superset.models.dashboard import Dashboard
    from superset.models.slice import Slice

    sm = app.appbuilder.sm
    username = os.getenv("ADMIN_USERNAME", "admin")
    admin = sm.find_user(username=username)
    if not admin:
        admin = sm.add_user(
            username=username,
            first_name="Local",
            last_name="Admin",
            email="admin@localhost.local",
            role=sm.find_role("Admin"),
            password=os.environ["SUPERSET_ADMIN_PASSWORD"],
        )
    # Отчётный экран обращается к витринам под отдельной учётной записью только для чтения.
    sources = {
        "Logistics · PostgreSQL": (
            f"postgresql+psycopg2://bi_reader:{quote(os.environ['BI_PASSWORD'], safe='')}@"
            f"{os.getenv('PGHOST', 'postgres')}:{os.getenv('PGPORT', '5432')}/"
            f"{os.getenv('PGDATABASE', 'deals_bigdata')}"
        ),
        "Logistics · ClickHouse": (
            f"clickhousedb://bi_reader:{quote(os.environ['BI_PASSWORD'], safe='')}@"
            f"{os.getenv('CLICKHOUSE_HOST', 'clickhouse')}:8123/mart"
        ),
    }
    databases = {}
    for name, uri in sources.items():
        database = db.session.query(Database).filter_by(database_name=name).one_or_none()
        if database is None:
            database = Database(database_name=name)
            db.session.add(database)
        database.set_sqlalchemy_uri(uri)
        database.expose_in_sqllab = True
        database.allow_dml = False
        databases[name] = database
    db.session.commit()
    labels = {
        "month": "Месяц",
        "origin_city": "Отправление",
        "destination_city": "Назначение",
        "body_group": "Группа кузова",
        "body_groups": "Совместимые кузова",
        "weight_category": "Вес",
        "p25_trip": "P25, ₽/рейс",
        "median_trip": "P50, ₽/рейс",
        "p75_trip": "P75, ₽/рейс",
        "avg_trip": "Средняя, ₽/рейс",
        "p25_km": "P25, ₽/км",
        "median_km": "P50, ₽/км",
        "p75_km": "P75, ₽/км",
        "trips_count": "Рейсы",
        "carriers_count": "Перевозчики",
        "cv_rate": "Коэф. вариации",
        "mom_pct": "MoM, %",
        "yoy_pct": "YoY, %",
        "last_observation_date": "Последнее наблюдение",
        "reliability_level": "Надёжность",
        "loading_date": "Дата погрузки",
        "price_id": "ID заявки",
        "prediction_kind": "Выборка",
        "actual_price_rub": "Факт, ₽",
        "predicted_price_rub": "Прогноз, ₽",
        "abs_error_rub": "Абс. ошибка, ₽",
        "ape_pct": "Ошибка, %",
        "as_of": "Снимок",
        "train_count": "Train, строк",
        "test_count": "Test, строк",
        "r2": "R²",
        "mape_pct": "MAPE, %",
        "rmse_rub": "RMSE, ₽",
        "mae_rub": "MAE, ₽",
    }
    # Рынок берём из ClickHouse, прогнозы по кузовам и качество модели — из PostgreSQL.
    datasets = {}
    for key, source, table in [
        ("market", "Logistics · ClickHouse", "market_corridor"),
        ("prediction", "Logistics · PostgreSQL", "ml_predictions_by_body"),
        ("quality", "Logistics · PostgreSQL", "ml_quality"),
    ]:
        database = databases[source]
        dataset = (
            db.session.query(SqlaTable)
            .filter_by(database_id=database.id, schema="mart", table_name=table)
            .one_or_none()
        )
        if dataset is None:
            dataset = SqlaTable(database=database, schema="mart", table_name=table, owners=[admin])
            db.session.add(dataset)
            db.session.flush()
        dataset.fetch_metadata()
        for column in dataset.columns:
            column.verbose_name = labels.get(column.column_name, column.column_name)
        datasets[key] = dataset
    db.session.commit()

    market_columns = [
        "month",
        "origin_city",
        "destination_city",
        "body_group",
        "weight_category",
        "p25_trip",
        "median_trip",
        "p75_trip",
        "avg_trip",
        "p25_km",
        "median_km",
        "p75_km",
        "trips_count",
        "carriers_count",
        "cv_rate",
        "mom_pct",
        "yoy_pct",
        "last_observation_date",
        "reliability_level",
    ]
    prediction_columns = [
        "loading_date",
        "price_id",
        "origin_city",
        "destination_city",
        "body_group",
        "body_groups",
        "prediction_kind",
        "actual_price_rub",
        "predicted_price_rub",
        "abs_error_rub",
        "ape_pct",
    ]
    number_columns = [
        "p25_trip",
        "median_trip",
        "p75_trip",
        "avg_trip",
        "p25_km",
        "median_km",
        "p75_km",
        "mom_pct",
        "yoy_pct",
        "actual_price_rub",
        "predicted_price_rub",
        "abs_error_rub",
        "ape_pct",
        "mape_pct",
        "rmse_rub",
        "mae_rub",
    ]
    column_config = {name: {"d3NumberFormat": ",.2f"} for name in number_columns}
    column_config.update({name: {"d3NumberFormat": ".3f"} for name in ["cv_rate", "r2"]})
    column_config["month"] = {"d3TimeFormat": "%Y-%m"}
    for name in ["trips_count", "carriers_count", "price_id", "train_count", "test_count"]:
        column_config[name] = {"d3NumberFormat": ",.0f"}
    # Описания пяти визуализаций: таблица сети, два графика, прогнозы и качество.
    # Таблицы запрашивают страницы с сервера, поэтому старые месяцы тоже доступны.
    specs = [
        (
            "Маршрутная сеть · коридор и доказательная база",
            "market",
            "table",
            {
                "query_mode": "raw",
                "all_columns": market_columns,
                "order_by_cols": [json.dumps([name, False]) for name in market_columns[:5]],
                "server_pagination": True,
                "server_page_length": 15,
            },
        ),
        (
            "Карточка маршрута · P25 / P50 / P75, ₽ за рейс",
            "market",
            "echarts_timeseries_line",
            {
                "x_axis": "month",
                "granularity_sqla": "month",
                "time_grain_sqla": "P1M",
                "metrics": [
                    metric("p25_trip", "P25"),
                    metric("median_trip", "P50"),
                    metric("p75_trip", "P75"),
                ],
                "groupby": ["origin_city", "destination_city", "body_group", "weight_category"],
                "show_legend": True,
                "y_axis_format": ",.0f",
            },
        ),
        (
            "Карточка маршрута · P25 / P50 / P75, ₽ за км",
            "market",
            "echarts_timeseries_line",
            {
                "x_axis": "month",
                "granularity_sqla": "month",
                "time_grain_sqla": "P1M",
                "metrics": [
                    metric("p25_km", "P25"),
                    metric("median_km", "P50"),
                    metric("p75_km", "P75"),
                ],
                "groupby": ["origin_city", "destination_city", "body_group", "weight_category"],
                "show_legend": True,
                "y_axis_format": ",.2f",
            },
        ),
        (
            "Факт и прогноз · тест и будущие заявки",
            "prediction",
            "table",
            {
                "query_mode": "raw",
                "all_columns": prediction_columns,
                "server_pagination": True,
                "server_page_length": 15,
                "order_by_cols": [
                    '["loading_date", false]',
                    '["price_id", false]',
                    '["body_group", false]',
                ],
            },
        ),
        (
            "Качество модели · временной holdout",
            "quality",
            "table",
            {
                "query_mode": "raw",
                "all_columns": [
                    "as_of",
                    "train_count",
                    "test_count",
                    "r2",
                    "mape_pct",
                    "rmse_rub",
                    "mae_rub",
                ],
                "page_length": 10,
            },
        ),
    ]
    charts = []
    for title, key, viz, options in specs:
        dataset = datasets[key]
        dataset_id = dataset.id
        chart = db.session.query(Slice).filter_by(slice_name=title).one_or_none()
        if chart is None:
            chart = Slice(
                slice_name=title,
                owners=[admin],
                datasource_id=dataset_id,
                datasource_type="table",
                viz_type=viz,
            )
            db.session.add(chart)
        chart.datasource_id = dataset_id
        chart.datasource_type = "table"
        chart.viz_type = viz
        # Без фильтров ограничиваем графики десятью сочетаниями, чтобы не перегружать браузер.
        chart.params = json.dumps(
            {
                "viz_type": viz,
                "datasource": f"{dataset.id}__table",
                "time_range": "No filter",
                "row_limit": 10000,
                **(
                    {
                        "series_limit": 10,
                        "series_limit_metric": metric("trips_count", "Макс. рейсов в месяце"),
                    }
                    if viz == "echarts_timeseries_line"
                    else {}
                ),
                "table_timestamp_format": "%Y-%m-%d",
                "column_config": column_config,
                "adhoc_filters": [],
                **options,
            },
            ensure_ascii=False,
        )
        charts.append(chart)
    db.session.flush()
    dashboard = db.session.query(Dashboard).filter_by(slug="smart-logistics").one_or_none()
    if dashboard is None:
        dashboard = Dashboard(
            slug="smart-logistics",
            dashboard_title="Smart Logistics · Рыночные ставки",
            owners=[admin],
        )
        db.session.add(dashboard)
    dashboard.published = True
    dashboard.slices = charts
    # Сверху размещаем пояснение, ниже — по одной визуализации на всю ширину строки.
    position = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {
            "type": "GRID",
            "id": "GRID_ID",
            "children": ["ROW-NOTES"],
            "parents": ["ROOT_ID"],
        },
        "HEADER_ID": {
            "type": "HEADER",
            "id": "HEADER_ID",
            "meta": {"text": dashboard.dashboard_title},
        },
        "ROW-NOTES": {
            "id": "ROW-NOTES",
            "type": "ROW",
            "parents": ["ROOT_ID", "GRID_ID"],
            "children": ["MARKDOWN-NOTES"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        },
        "MARKDOWN-NOTES": {
            "id": "MARKDOWN-NOTES",
            "type": "MARKDOWN",
            "parents": ["ROOT_ID", "GRID_ID", "ROW-NOTES"],
            "children": [],
            "meta": {
                "width": 12,
                "height": 24,
                "code": "**Снимок: 26.10.2026 · RUB без НДС.** Фильтры слева управляют сетью, "
                "графиками и прогнозами. Сбросьте фильтры для всей сети; таблицы читают "
                "все строки постранично. Графики показывают до 10 сочетаний "
                "маршрута/кузова/веса по максимальному числу рейсов за месяц; "
                "выберите одну комбинацию для подробного анализа. "
                "Качество модели рассчитано по полному test.\n\n"
                "**Надёжность:** HIGH — ≥30 рейсов, ≥5 перевозчиков, свежесть ≤30 дней; "
                "MEDIUM — ≥10 рейсов; иначе LOW_DATA. Пропуск MoM/YoY означает отсутствие "
                "календарного сравнения. Последний день ещё не прошёл разметку выбросов.\n\n"
                "**Прогнозы:** test — отложенный период, future — будущая заявка без факта "
                "и ошибки. При сброшенном фильтре кузова многокузовная заявка видна под "
                "каждой совместимой группой. Не суммируйте рейсы между кузовами/месяцами "
                "и не усредняйте готовые медианы.",
            },
        },
    }
    for index, chart in enumerate(charts):
        row, component = f"ROW-{index}", f"CHART-{chart.id}"
        position["GRID_ID"]["children"].append(row)
        position[row] = {
            "id": row,
            "type": "ROW",
            "parents": ["ROOT_ID", "GRID_ID"],
            "children": [component],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        position[component] = {
            "id": component,
            "type": "CHART",
            "parents": ["ROOT_ID", "GRID_ID", row],
            "children": [],
            "meta": {"chartId": chart.id, "width": 12, "height": 60, "sliceName": chart.slice_name},
        }
    dashboard.position_json = json.dumps(position, ensure_ascii=False)
    filters = []
    # Каждая линия соответствует полной ячейке: её готовая медиана не смешивается с другими.
    # Фильтры действуют на сеть, графики и прогнозы; общие метрики качества остаются неизменными.
    for column, label, default in [
        ("origin_city", "Отправление", "Москва"),
        ("destination_city", "Назначение", "Санкт-Петербург"),
        ("body_group", "Группа кузова", "Тент"),
        ("weight_category", "Весовая категория", "FTL"),
    ]:
        filters.append(
            {
                "id": f"NATIVE_FILTER-{column}",
                "name": label,
                "filterType": "filter_select",
                "targets": [{"datasetId": datasets["market"].id, "column": {"name": column}}],
                "defaultDataMask": {
                    "extraFormData": {"filters": [{"col": column, "op": "IN", "val": [default]}]},
                    "filterState": {"value": [default], "label": default},
                },
                "controlValues": {
                    "multiSelect": False,
                    "enableEmptyFilter": False,
                    "defaultToFirstItem": False,
                    "searchAllOptions": True,
                },
                "cascadeParentIds": [],
                "scope": {"rootPath": ["ROOT_ID"], "excluded": [charts[4].id]},
                "type": "NATIVE_FILTER",
                "description": "Фильтр сети, графиков и прогнозов.",
            }
        )
    dashboard.json_metadata = json.dumps(
        {
            "native_filter_configuration": filters,
            "refresh_frequency": 0,
            "color_scheme": "supersetColors",
        },
        ensure_ascii=False,
    )
    dashboard.description = (
        "Снимок 2026-10-26. Цены в RUB без НДС. Серверная пагинация всех ячеек; "
        "фильтры выбирают маршрут, кузов и вес для сети, графиков и прогнозов. "
        "Нельзя усреднять готовые медианы. У будущих заявок факт и ошибки отсутствуют."
    )
    db.session.commit()
    print("Dashboard provisioned: /superset/dashboard/smart-logistics/")
