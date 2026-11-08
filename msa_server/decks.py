"""Normalización y persistencia transaccional de mazos por jugador."""

from __future__ import annotations

import json
import logging

from .storage import atomic_write, client_data_file, client_lock, load_client_json
from .inventory import (INCOMPATIBLE_UNIT_IDS, PLAYABLE_UNIT_CARRIERS,
                        PLAYABLE_UNIT_SURROGATES)


def normalized_unit_id(unit_id: int) -> int:
    normalized = PLAYABLE_UNIT_SURROGATES.get(unit_id, unit_id)
    normalized = PLAYABLE_UNIT_CARRIERS.get(normalized, normalized)
    return -1 if normalized in INCOMPATIBLE_UNIT_IDS else normalized


def expanded_ints(values: list[str], limit: int) -> list[int]:
    parts = []
    for value in values:
        parts.extend(value.split(","))
    result = []
    for value in parts[:limit]:
        try:
            result.append(int(value))
        except ValueError:
            result.append(0)
    return result


def normalized_ints(values, limit: int, fill: int) -> list[int]:
    if not isinstance(values, (list, tuple)):
        return [fill] * limit
    result: list[int] = []
    for value in values[:limit]:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            result.append(fill)
    result.extend([fill] * (limit - len(result)))
    return result


def load_decks(client: str) -> list:
    """Carga mazos y migra IDs auxiliares antiguas a sus unidades reales."""
    with client_lock(client):
        decks = load_client_json(client, "user_deck.json", [])
        if not isinstance(decks, list):
            return []
        changed = False
        for deck in decks:
            if not isinstance(deck, dict) or not isinstance(deck.get("unit_ids"), list):
                continue
            normalized = []
            for value in deck["unit_ids"]:
                try:
                    unit_id = int(value)
                except (TypeError, ValueError):
                    unit_id = -1
                real_id = normalized_unit_id(unit_id)
                normalized.append(real_id)
                changed |= real_id != value
            deck["unit_ids"] = normalized
        if changed:
            atomic_write(client_data_file(client, "user_deck.json"), json.dumps(
                decks, separators=(",", ":")
            ).encode("utf-8"))
        return decks


def persist_decks(params: dict[str, list[str]], client: str) -> None:
    with client_lock(client):
        decks = load_decks(client)
        if not isinstance(decks, list):
            decks = []
        while len(decks) < 26:
            decks.append({"cover": 0, "unit_ids": [-1] * 10,
                          "deck_attribute": [0] * 10})
        changed_decks: list[int] = []
        for deck_no in range(1, 27):
            units_key = f"deck_unit_{deck_no}[]"
            attrs_key = f"deck_attribute_{deck_no}[]"
            if units_key not in params and attrs_key not in params:
                continue
            previous = decks[deck_no - 1]
            if not isinstance(previous, dict):
                previous = {}
            updated = dict(previous)
            old_units = normalized_ints(previous.get("unit_ids"), 10, -1)
            old_attrs = normalized_ints(previous.get("deck_attribute"), 10, 0)
            if units_key in params:
                received_units = [normalized_unit_id(unit_id) for unit_id in
                                  expanded_ints(params[units_key], 10)]
                old_units[:len(received_units)] = received_units
            if attrs_key in params:
                received_attrs = expanded_ints(params[attrs_key], 10)
                old_attrs[:len(received_attrs)] = received_attrs
            cover_values = params.get(f"cover_{deck_no}", params.get("cover", []))
            cover = previous.get("cover", 0)
            if cover_values:
                try:
                    cover = max(0, int(cover_values[0]))
                except (TypeError, ValueError):
                    pass
            updated.update({"cover": cover, "unit_ids": old_units,
                            "deck_attribute": old_attrs})
            decks[deck_no - 1] = updated
            changed_decks.append(deck_no)
        if changed_decks:
            target = client_data_file(client, "user_deck.json")
            if target.is_file():
                try:
                    json.loads(target.read_text(encoding="utf-8"))
                    atomic_write(client_data_file(client, "user_deck.backup.json"),
                                 target.read_bytes())
                except (OSError, UnicodeError, json.JSONDecodeError):
                    logging.exception("No se pudo respaldar el mazo anterior")
            atomic_write(target, json.dumps(
                decks, separators=(",", ":")
            ).encode("utf-8"))
            logging.info("DECK_SAVE cliente=%s mazos=%s", client, changed_decks)
