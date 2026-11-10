"""Читает параметры подключения, пароли и границы обрабатываемого периода."""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def database_url(
    database: str | None = None,
    driver: str = "postgresql",
    user: str | None = None,
    password: str | None = None,
) -> str:
    username = user or os.getenv("PGUSER", "logistics")
    password = password or os.getenv("PGPASSWORD") or os.environ["POSTGRES_PASSWORD"]
    return (
        f"{driver}://{quote(username, safe='')}:{quote(password, safe='')}@"
        f"{os.getenv('PGHOST', 'postgres')}:{os.getenv('PGPORT', '5432')}/"
        f"{quote(database or os.getenv('PGDATABASE', 'deals_bigdata'), safe='')}"
    )


@dataclass(frozen=True)
class Settings:
    """Дата снимка и период погрузки: начало включено, конец не включён."""

    as_of: date
    start: date | None = None
    end: date | None = None

    def __post_init__(self):
        if self.start and self.end and self.start >= self.end:
            raise ValueError("start_date must be earlier than exclusive end_date")

    @classmethod
    def from_env(cls):
        # Фиксированная дата снимка сохраняет те же выборки при запуске в другой день.
        return cls(
            date.fromisoformat(os.getenv("AS_OF", "2026-10-26")),
            date.fromisoformat(os.environ["START_DATE"]) if os.getenv("START_DATE") else None,
            date.fromisoformat(os.environ["END_DATE"]) if os.getenv("END_DATE") else None,
        )


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["airflow-url"]:
        print(database_url("airflow", "postgresql+psycopg2"))
