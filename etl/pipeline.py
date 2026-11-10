"""Выполняет SQL очистки и расчёта витрин, собирает число строк и причины отсева."""

from etl.db import operation, sql_file


def load_core(settings):
    """Пересогласует выбранный период с источником и записывает статистику проверок."""
    with operation("etl", settings) as (conn, stats):
        conn.execute(sql_file("core/load.sql"), {"start": settings.start, "end": settings.end})
        stats["rows_read"] = conn.execute("SELECT count(*) FROM batch_source").fetchone()[0]
        stats["rows_normalized"] = conn.execute(
            "SELECT count(*) FROM batch_source WHERE cardinality(physical_reasons||monetary_reasons)=0"
        ).fetchone()[0]
        stats["rows_quarantined"] = stats["rows_read"] - stats["rows_normalized"]
        stats["reasons"] = dict(
            conn.execute(
                "SELECT reason,count(*) FROM batch_source, "
                "unnest(physical_reasons||monetary_reasons) reason GROUP BY reason"
            ).fetchall()
        )
        # Обновляем статистику PostgreSQL, чтобы последующие запросы выбирали быстрый план.
        conn.execute(
            "ANALYZE core.shipments; ANALYZE core.prices; "
            "ANALYZE core.price_dates; ANALYZE core.price_body_groups"
        )
    return stats


def build_marts(settings):
    """Пересчитывает всю рыночную витрину на указанную дату снимка."""
    with operation("mart", settings) as (conn, stats):
        conn.execute(sql_file("mart/market_corridor.sql"), {"as_of": settings.as_of})
        stats["observations"] = conn.execute("SELECT count(*) FROM observations").fetchone()[0]
        stats["route_cells"] = conn.execute("SELECT count(*) FROM mart.market_corridor").fetchone()[
            0
        ]
        conn.execute("ANALYZE mart.market_corridor")
    return stats
