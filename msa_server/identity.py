"""Resolución de identidad estable de una instalación del cliente."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Mapping

from .storage import migrate_client_data, register_legacy_ip


DEVICE_TERMINAL_PREFIX = "msa-device-"
INVALID_TERMINAL_IDS = {"", "0", "null", "none", "msa-offline-local"}


@dataclass(frozen=True)
class IdentityResolution:
    client: str
    new_cookie: str = ""


def public_player_id(client: str) -> str:
    """Identificador público estable usado por Online y el futuro Social."""
    return "msa-" + hashlib.sha256(client.encode("utf-8")).hexdigest()[:16]


def _valid_device_token(value: str) -> str:
    token = value.lower().strip()
    if (len(token) == 32
            and all(char in "0123456789abcdef" for char in token)):
        return token
    return ""


def device_cookie(headers: Mapping[str, str]) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
    except Exception:
        return ""
    value = cookie.get("msa_device")
    return _valid_device_token(value.value if value else "")


def registration_terminal_id(headers: Mapping[str, str],
                             new_cookie: str = "") -> str:
    """Emite el token hexadecimal que acepta el campo nativo terminal_id."""
    token = device_cookie(headers) or _valid_device_token(new_cookie)
    return token or uuid.uuid4().hex


def resolve_client_identity(headers: Mapping[str, str],
                            source_ip: str) -> IdentityResolution:
    """Resuelve una instalación sin usar la IP como identidad permanente."""
    terminal_id = headers.get("terminal_id", "").strip()
    fingerprint = (
        headers.get("devicemodel", ""),
        headers.get("osversion", ""),
        headers.get("terminal_type", ""),
        headers.get("isr", ""),
        headers.get("User-Agent", ""),
    )
    fingerprint_material = "|".join(fingerprint)
    if not any(fingerprint):
        fingerprint_material = f"legacy-ip:{source_ip}"
    fallback_identity = "client-" + hashlib.sha256(
        f"fingerprint:{fingerprint_material}".encode(
            "utf-8", errors="replace"
        )
    ).hexdigest()[:24]

    new_cookie = ""
    issued_token = ""
    if terminal_id.lower().startswith(DEVICE_TERMINAL_PREFIX):
        issued_token = _valid_device_token(
            terminal_id[len(DEVICE_TERMINAL_PREFIX):]
        )
    else:
        # El cliente original acepta el identificador hexadecimal sin el
        # prefijo interno del servidor. Los IDs prefijados antiguos continúan
        # siendo válidos por la rama anterior.
        issued_token = _valid_device_token(terminal_id)
    if issued_token:
        # El token emitido puede volver como terminal_id o como cookie.
        # Ambos caminos deben representar exactamente la misma instalación.
        material = f"cookie:{issued_token}"
    elif terminal_id.lower() not in INVALID_TERMINAL_IDS:
        material = f"terminal:{terminal_id}"
    else:
        cookie_id = device_cookie(headers)
        if cookie_id:
            material = f"cookie:{cookie_id}"
        else:
            material = f"fingerprint:{fingerprint_material}"
            new_cookie = uuid.uuid4().hex

    identity = "client-" + hashlib.sha256(
        material.encode("utf-8", errors="replace")
    ).hexdigest()[:24]
    register_legacy_ip(identity, source_ip)
    # Un terminal emitido durante un alta limpia no debe heredar el estado de
    # la huella provisional. Dos instalaciones idénticas compartirían esa
    # huella y ambas copiarían la misma cuenta que precisamente se quiere
    # separar. Las identidades antiguas conservan la migración de compatibilidad.
    if not issued_token:
        migrate_client_data(identity, fallback_identity, source_ip)
    return IdentityResolution(identity, new_cookie)
