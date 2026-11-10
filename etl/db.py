"""Подключение к PostgreSQL, чтение SQL и журнал выполнения этапов."""

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from etl.config import ROOT, database_url

LOCK_ID = 20261026
log = logging.getLogger("logistics")


def event(stage, **fields):
    log.info(
        json.dumps(
            {"timestamp": datetime.now(timezone.utc).isoformat(), "stage": stage, **fields},
            default=str,
            ensure_ascii=False,
        )
    )


def connect(**kwargs):
    return psycopg.connect(database_url(), cursor_factory=psycopg.ClientCursor, **kwargs)


def sql_file(name):
    return (ROOT / "sql" / name).read_text()


@contextmanager
def operation(stage, settings):
    """Сохраняет этап целиком либо отменяет его; отдельно записывает результат в журнал."""
    run_id, started, stats = str(uuid4()), monotonic(), {}
    # Запись о запуске сохраняется отдельно и останется даже при ошибке обработки.
    with connect() as audit:
        audit.execute(
            "INSERT INTO ops.runs(run_id, stage, as_of, start_date, end_date) "
            "VALUES (%s,%s,%s,%s,%s)",
            (run_id, stage, settings.as_of, settings.start, settings.end),
        )
    event(stage, status="started", run_id=run_id)
    try:
        with connect() as conn:
            # Одна блокировка не даёт двум запускам одновременно менять общие таблицы.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            yield conn, stats
    except Exception as exc:
        with connect() as audit:
            audit.execute(
                "UPDATE ops.runs SET status='failed', finished_at=now(), "
                "details=%s WHERE run_id=%s",
                (Jsonb({"error_type": type(exc).__name__, "error": str(exc)[:1500]}), run_id),
            )
        event(stage, status="failed", run_id=run_id, error_type=type(exc).__name__)
        raise
    else:
        stats["duration_seconds"] = round(monotonic() - started, 3)
        with connect() as audit:
            audit.execute(
                "UPDATE ops.runs SET status='success', finished_at=now(), "
                "details=%s WHERE run_id=%s",
                (Jsonb(stats), run_id),
            )
        event(stage, status="success", run_id=run_id, **stats)
