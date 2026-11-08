"""Contrato compartido del historial PvP que parsea MSA 7.13.0."""

from __future__ import annotations

from collections.abc import Iterable


MAX_PVP_RECORDS = 5


def battle_record(persons: Iterable[dict], battle_log: str, result: int,
                  battle_time: str) -> dict:
    """Construye OnlineBattleResult; el cliente nombra jugadores 1p..4p."""
    record = {
        f"{slot}p": person
        for slot, person in enumerate(persons, 1)
        if slot <= 4 and isinstance(person, dict)
    }
    record.update({
        "battle_log": str(battle_log),
        "result": int(result),
        "battle_time": str(battle_time),
    })
    return record


def normalise_history(value, limit: int = MAX_PVP_RECORDS) -> list[dict]:
    """Migra user1..user4 antiguos al contrato nativo 1p..4p."""
    if not isinstance(value, list):
        return []
    history = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        record = dict(raw)
        for slot in range(1, 5):
            legacy = f"user{slot}"
            native = f"{slot}p"
            if native not in record and isinstance(record.get(legacy), dict):
                record[native] = record[legacy]
            record.pop(legacy, None)
        record.pop("timestamp", None)
        if not isinstance(record.get("1p"), dict):
            continue
        if not isinstance(record.get("battle_log"), str):
            record["battle_log"] = ""
        try:
            record["result"] = int(record.get("result", 0))
        except (TypeError, ValueError):
            record["result"] = 0
        if not isinstance(record.get("battle_time"), str):
            continue
        history.append(record)
    return history[:limit]
