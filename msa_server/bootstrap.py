"""Preparacion del estado autoritativo antes de despachar una solicitud."""

from __future__ import annotations

from .storage import ensure_client_defaults


def prepare_client(client: str, source_ip: str) -> None:
    """Hace utilizables identidades nuevas y completa identidades recientes."""
    ensure_client_defaults(client, f"PLAYER {source_ip}")
