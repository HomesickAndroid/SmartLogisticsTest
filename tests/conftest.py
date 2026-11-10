import os
from datetime import date
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from etl.config import database_url
from etl.db import connect, sql_file


@pytest.fixture
def database(monkeypatch):
    """Для каждой проверки создаёт отдельную базу; демонстрационные данные не затрагиваются."""
    if not os.getenv("PGHOST"):
        pytest.skip("Set PGHOST or use docker compose run --rm tests")
    database_name = "test_logistics_" + uuid4().hex
    with psycopg.connect(database_url(), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        monkeypatch.setenv("PGDATABASE", database_name)
        try:
            with connect() as conn:
                conn.execute("CREATE SCHEMA raw; SET search_path=raw,public")
                conn.execute(sql_file("source/schema.sql"))
                conn.execute(sql_file("core/schema.sql"))
                conn.execute(sql_file("mart/schema.sql"))
                conn.execute("""INSERT INTO raw.cities(id,gc_id,name,full_name_local,full_name_gc,latitude,longitude)
                    VALUES(1,1001,'Москва','Москва, г. Москва','Москва',55.7,37.6),
                          (2,1003,'Казань','Казань, Республика Татарстан','Казань',55.8,49.1),
                          (3,1001,'Дубль','Дубль, Неверный регион','Дубль',0,0)""")
                conn.execute("""INSERT INTO raw.body_types(id,name,"id_from_1C",std_id)
                    SELECT id,name,id,std_id FROM (VALUES
                    (56,'Тент',56),(57,'Бок',56),(61,'Полный',56),(1,'Бортовая',15),
                    (15,'Борт',15),(4,'Реф',4),(46,'Мультирежимный',4),(2,'Изотерм',2)) v(id,name,std_id)""")
                conn.execute(
                    "INSERT INTO raw.exchange_rates(num_code,value,setting_date) VALUES ('840',90,'2026-10-02')"
                )
            yield database_name
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name))
            )


@pytest.fixture
def add_price(database):
    """Добавляет тестовую сделку с заданными отклонениями, датами и кузовами."""

    def insert(identifier, dates=None, bodies=None, **changes):
        values = {
            "id": identifier,
            "loading_date": date(2026, 10, 5),
            "loading_city_gcid": 1001,
            "unloading_city_gcid": 1003,
            "distance": 100.0,
            "total_weight": 20.0,
            "total_volume": 80.0,
            "price": 10000.0,
            "currency_code": "643",
            "body_type": 56,
            "price_type": 3,
            "organization_id": identifier,
            "is_fact": True,
            "complex": False,
            "outlier_mark": False,
        }
        values.update(changes)
        with connect() as conn:
            conn.execute(
                sql.SQL("INSERT INTO raw.prices ({}) VALUES ({})").format(
                    sql.SQL(",").join(map(sql.Identifier, values)),
                    sql.SQL(",").join(sql.Placeholder() for _ in values),
                ),
                tuple(values.values()),
            )
            for day in dates if dates is not None else [values["loading_date"]]:
                conn.execute(
                    "INSERT INTO raw.price_dates(price_id,price_date) VALUES (%s,%s)",
                    (identifier, day),
                )
            for body in bodies or []:
                conn.execute(
                    "INSERT INTO raw.price_body_types(price_id,body_type_id) VALUES (%s,%s)",
                    (identifier, body),
                )

    return insert
