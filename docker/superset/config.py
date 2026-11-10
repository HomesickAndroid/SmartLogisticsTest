"""Настройки Superset: служебная база, ключ сессии и ограничения отчётных запросов."""

import os
from urllib.parse import quote

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://{quote(os.getenv('PGUSER', 'logistics'), safe='')}:"
    f"{quote(os.environ['POSTGRES_PASSWORD'], safe='')}@{os.getenv('PGHOST', 'postgres')}:"
    f"{os.getenv('PGPORT', '5432')}/superset"
)
WTF_CSRF_ENABLED = True
FEATURE_FLAGS = {"DASHBOARD_NATIVE_FILTERS": True}
SQLLAB_TIMEOUT = 120
# Superset 5 применяет ROW_LIMIT и к подсчёту строк при постраничном просмотре.
# Лимит выше 203 644 ячеек примера; отдельная страница по-прежнему содержит 15 строк.
ROW_LIMIT = 1000000
SQL_MAX_ROW = 1000000
SUPERSET_WEBSERVER_TIMEOUT = 120
SESSION_COOKIE_SAMESITE = "Lax"
TALISMAN_ENABLED = False  # Локальный HTTP; порты доступны только с этого компьютера.
ENABLE_PROXY_FIX = False
