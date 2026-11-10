"""Проверки конфигурации без сервиса и файлов секретов."""

from urllib.parse import quote

import pytest

from etl.config import database_url


def test_database_url_uses_environment_and_escapes_password(monkeypatch):
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "пароль:@/?#")
    monkeypatch.setenv("PGUSER", "user@local")
    monkeypatch.setenv("PGHOST", "postgres")
    monkeypatch.setenv("PGPORT", "5432")
    monkeypatch.setenv("PGDATABASE", "test_db")
    assert database_url() == (
        f"postgresql://user%40local:{quote('пароль:@/?#', safe='')}@postgres:5432/test_db"
    )


def test_database_password_is_required(monkeypatch):
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(KeyError, match="POSTGRES_PASSWORD"):
        database_url()
