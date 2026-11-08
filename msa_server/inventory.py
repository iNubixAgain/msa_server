"""Catálogo de unidades poseídas compartido por Deck y Build Up."""

from __future__ import annotations

import json

from .config import SERVER_DEFAULTS
from .storage import (atomic_write, client_data_file, client_lock,
                      read_json_file)


# Unidades presentes en los maestros del cliente pero ausentes del inventario
# offline original. Deben atravesar el mismo contrato que el resto de unidades
# para que Deck y Unit Up/Build Up construyan una única colección coherente.
EXTRA_PLAYABLE_UNIT_IDS: tuple[int, ...] = ()

# IDs auxiliares publicados en los maestros para que las variantes que el
# cliente considera internas puedan entrar en sus listas de Unit/Deck. Al
# persistir un mazo se convierten de nuevo a la ID real.
PLAYABLE_UNIT_SURROGATES: dict[int, int] = {}

# La 935 ya pertenece al catalogo coleccionable reconocido por el cliente.
# Se publica visualmente como 936 y se normaliza al guardar el mazo.
PLAYABLE_UNIT_CARRIERS: dict[int, int] = {}

INCOMPATIBLE_UNIT_IDS = {
    67, 103, 155, 175, 294, 301, 302, 413, 936,
    *range(3001, 3010),
}

_UNIT_TIME = "2023-01-12 15:30:00"

# Los cuatro BUFF Items que el cliente 7.13.0 publica para unidades
# compatibles escriben en estas posiciones (tipo - 1) de status_seed.
BUFF_STATUS_SEED_TYPES = frozenset({5, 6, 8, 9})
MAX_STATUS_SEED_LEVEL = 10


def _owned_unit(unit_id: int) -> dict:
    return {
        "param": {
            "unit_id": unit_id,
            "level": 50,
            "evolution": 5,
            "version": 5,
            "equip": [1, 1, 1, 1, 1, 1],
            "skill": [50, 50, 50, 50],
            "status_seed": [10, 10, 10, 10, 0, 0, 0, 0, 0],
        },
        "get_time": _UNIT_TIME,
        "update_time": _UNIT_TIME,
    }


def _default_inventory() -> dict:
    return read_json_file(SERVER_DEFAULTS / "user_units.json", {"unit": []})


def owned_units_payload(client: str | None = None) -> dict:
    """Devuelve el inventario autoritativo, persistente y aislado del cliente."""
    if client:
        target = client_data_file(client, "user_units.json")
        with client_lock(client):
            source = (read_json_file(target, _default_inventory())
                      if target.is_file() else _default_inventory())
            result = _merge_requested_units(source)
            # Materializa el inventario en el servidor. El APK nunca vuelve a
            # ser la fuente de verdad de unidades para esta instalacion.
            atomic_write(target, json.dumps(
                result, separators=(",", ":")
            ).encode("utf-8"))
            return result
    return _merge_requested_units(_default_inventory())


def upgrade_status_seeds(client: str, unit_id: int,
                         seed_types: list[int],
                         increments: list[int]) -> bool:
    """Aplica y persiste de forma atómica los BUFF Items de una unidad."""
    if (not client or unit_id <= 0 or not seed_types
            or len(seed_types) != len(increments)
            or len(set(seed_types)) != len(seed_types)
            or any(value not in BUFF_STATUS_SEED_TYPES for value in seed_types)
            or any(value <= 0 for value in increments)):
        return False

    target = client_data_file(client, "user_units.json")
    with client_lock(client):
        source = (read_json_file(target, _default_inventory())
                  if target.is_file() else _default_inventory())
        inventory = _merge_requested_units(source)
        unit = next((
            entry for entry in inventory["unit"]
            if int(entry.get("param", {}).get("unit_id", -1)) == unit_id
        ), None)
        if unit is None:
            return False

        param = unit["param"]
        raw_seeds = param.get("status_seed", [])
        seeds = []
        for index in range(9):
            try:
                value = int(raw_seeds[index])
            except (IndexError, TypeError, ValueError):
                value = 0
            seeds.append(max(0, min(value, MAX_STATUS_SEED_LEVEL)))

        for seed_type, increment in zip(seed_types, increments):
            index = seed_type - 1
            seeds[index] = min(
                MAX_STATUS_SEED_LEVEL, seeds[index] + increment
            )
        param["status_seed"] = seeds
        atomic_write(target, json.dumps(
            inventory, separators=(",", ":")
        ).encode("utf-8"))
        return True


def _merge_requested_units(source: dict) -> dict:
    raw_units = source.get("unit", []) if isinstance(source, dict) else []
    defaults = _default_inventory().get("unit", [])
    default_by_id = {
        int(entry.get("param", {}).get("unit_id", -1)): entry
        for entry in defaults
        if isinstance(entry, dict) and isinstance(entry.get("param"), dict)
    }
    allowed_ids = set(default_by_id) - INCOMPATIBLE_UNIT_IDS
    units = [
        entry for entry in raw_units
        if isinstance(entry, dict)
        and int(entry.get("param", {}).get("unit_id", -1)) in allowed_ids
    ]
    present = {
        entry.get("param", {}).get("unit_id")
        for entry in units
        if isinstance(entry.get("param"), dict)
    }
    units.extend(default_by_id[unit_id] for unit_id in allowed_ids
                 if unit_id not in present)
    units.sort(key=lambda entry: int(entry.get("param", {}).get("unit_id", 0)))
    return {"unit": units}
