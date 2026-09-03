"""Reloj de servidor compatible con las fechas JST del cliente MSA."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


JST = timezone(timedelta(hours=9), name="JST")
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def jst_now_string(now: datetime | None = None) -> str:
    """Devuelve la hora actual de Japón con el formato nativo del cliente."""
    instant = now if now is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(JST).strftime(DATETIME_FORMAT)


def response_clock(now: datetime | None = None) -> dict[str, object]:
    """Genera un bloque de respuesta cuyo reloj avanza en cada petición."""
    current = jst_now_string(now)
    return {
        "error_code": 0,
        "now_time": current,
        "server_time": current,
    }
