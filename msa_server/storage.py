"""Persistencia JSON centralizada y aislada por instalación."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from .config import DATA, SERVER_CONTENT, SERVER_DEFAULTS

_legacy_client_ips: dict[str, str] = {}
_client_locks_guard = threading.Lock()
_client_locks: dict[str, threading.RLock] = {}


def atomic_write(path: Path, body: bytes) -> None:
    """Reemplaza un archivo solamente después de completar su escritura."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(body)
            tmp.flush()
            os.fsync(tmp.fileno())
            temp = Path(tmp.name)
        temp.replace(path)
        temp = None
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                logging.exception("No se pudo limpiar el temporal %s", temp)


def read_json_file(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        logging.exception("JSON persistente ilegible; usando fallback: %s", path)
        return fallback


def response_file(name: str) -> Path | None:
    saved = DATA / name
    bundled = SERVER_CONTENT / name
    if saved.is_file():
        return saved
    if bundled.is_file():
        return bundled
    return None


def load_json(name: str, fallback):
    target = response_file(name)
    if not target:
        return fallback
    return read_json_file(target, fallback)


def client_data_file(client: str, name: str) -> Path:
    key = hashlib.sha256(client.encode("utf-8")).hexdigest()[:16]
    return DATA / "clients" / key / name


def client_lock(client: str) -> threading.RLock:
    with _client_locks_guard:
        return _client_locks.setdefault(client, threading.RLock())


def register_legacy_ip(client: str, source_ip: str) -> None:
    """Conserva la ubicación del estado antiguo basado en IP para migrarlo."""
    with _client_locks_guard:
        _legacy_client_ips[client] = source_ip


def migrate_client_data(client: str, *sources: str) -> None:
    """Adopta una sola vez perfil y mazos de identidades anteriores."""
    with client_lock(client):
        for source_client in sources:
            if not source_client or source_client == client:
                continue
            for name in ("profile.json", "user_deck.json", "user_units.json",
                         "user_deck.backup.json"):
                target = client_data_file(client, name)
                source = client_data_file(source_client, name)
                if target.exists() or not source.is_file():
                    continue
                try:
                    # Solo migrar JSON válido; nunca propagar corrupción antigua.
                    json.loads(source.read_text(encoding="utf-8"))
                    atomic_write(target, source.read_bytes())
                    logging.info("Estado migrado a identidad de dispositivo: %s",
                                 name)
                except (OSError, UnicodeError, json.JSONDecodeError):
                    logging.exception("No se pudo migrar %s para %s", name, client)


def ensure_client_defaults(client: str, default_name: str = "") -> None:
    """Materializa y repara el estado minimo autoritativo de cada cliente."""
    if not client:
        return
    with client_lock(client):
        # 1. profile.json
        profile_file = client_data_file(client, "profile.json")
        profile_data = read_json_file(profile_file, {}) if profile_file.is_file() else {}
        if not isinstance(profile_data, dict):
            profile_data = {}
        profile_defaults = {
                "name": default_name if default_name else f"PLAYER {client[:6]}",
                "avatar": 1,
                "language": 8,
                "profile": "MSA LAN",
                "play_type": 0,
                "is_accept": 0,
                "base_avatar": 1,
        }
        repaired_profile = {**profile_defaults, **profile_data}
        if repaired_profile != profile_data:
            atomic_write(
                profile_file,
                json.dumps(repaired_profile, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8"),
            )

        # 2. user_deck.json & user_deck.backup.json
        deck_file = client_data_file(client, "user_deck.json")
        backup_file = client_data_file(client, "user_deck.backup.json")
        authoritative_deck = SERVER_DEFAULTS / "user_deck.json"
        deck_bytes = (authoritative_deck.read_bytes()
                      if authoritative_deck.is_file() else b"[]")
        deck_data = read_json_file(deck_file, None) if deck_file.is_file() else None
        if not isinstance(deck_data, list):
            atomic_write(deck_file, deck_bytes)
        backup_data = (read_json_file(backup_file, None)
                       if backup_file.is_file() else None)
        if not isinstance(backup_data, list):
            atomic_write(backup_file, deck_bytes)

        # 3. user_units.json
        units_file = client_data_file(client, "user_units.json")
        units_data = read_json_file(units_file, None) if units_file.is_file() else None
        if (not isinstance(units_data, dict)
                or not isinstance(units_data.get("unit"), list)):
            authoritative_units = SERVER_DEFAULTS / "user_units.json"
            units_bytes = (authoritative_units.read_bytes()
                           if authoritative_units.is_file() else b'{"unit":[]}')
            atomic_write(units_file, units_bytes)

        # 4. online.json
        online_file = client_data_file(client, "online.json")
        online_data = read_json_file(online_file, {}) if online_file.is_file() else {}
        if not isinstance(online_data, dict):
            online_data = {}
        online_defaults = {
                "online_score": 0,
                "1on1_win": 0,
                "1on1_lose": 0,
                "2on2_win": 0,
                "2on2_lose": 0,
                "history_1on1": [],
                "history_2on2": [],
        }
        repaired_online = {**online_defaults, **online_data}
        if repaired_online != online_data:
            atomic_write(
                online_file,
                json.dumps(repaired_online, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8"),
            )

        # 4b. arena.json (Rank Match)
        arena_file = client_data_file(client, "arena.json")
        arena_data = read_json_file(arena_file, {}) if arena_file.is_file() else {}
        if not isinstance(arena_data, dict):
            arena_data = {}
        arena_defaults = {
                "season_no": 58, "rank": 1, "class": 3, "order": 0,
                "rating": 0, "token": "msa-arena-init", "win": 0,
                "win3": 0, "win_beat": 0, "best_rank": 1,
                "best_class": 3, "penalty_time": "2000-01-01 00:00:00",
                "penalty_rating": 0, "penalty_reason": 0, "cheat_times": 0,
                "battle_win": 0, "battle_lose": 0, "history": [],
                "honor_history": [], "reported_rooms": [],
        }
        repaired_arena = {**arena_defaults, **arena_data}
        if repaired_arena != arena_data:
            atomic_write(
                arena_file,
                json.dumps(repaired_arena, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8"),
            )

        # 5. ultimate_mission.json
        ult_file = client_data_file(client, "ultimate_mission.json")
        ult_data = read_json_file(ult_file, {}) if ult_file.is_file() else {}
        if not isinstance(ult_data, dict):
            ult_data = {}
        ult_defaults = {
                "chance": 3, "reset_num": 0, "max_reset_num": 3,
                "next_update_time": "2099-12-31 23:59:59", "attack_affection": 0,
                "deck": [
                    {"affection": 0, "deck_id": 1, "rank": 0},
                    {"affection": 1, "deck_id": 1, "rank": 0},
                    {"affection": 2, "deck_id": 1, "rank": 0},
                    {"affection": 3, "deck_id": 1, "rank": 0},
                    {"affection": 4, "deck_id": 1, "rank": 0},
                ],
        }
        repaired_ult = {**ult_defaults, **ult_data}
        if repaired_ult != ult_data:
            atomic_write(
                ult_file,
                json.dumps(repaired_ult, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8"),
            )

        # 6. battle_tactics.json
        tactics_file = client_data_file(client, "battle_tactics.json")
        tactics_data = (read_json_file(tactics_file, {})
                        if tactics_file.is_file() else {})
        if not isinstance(tactics_data, dict):
            tactics_data = {}
        tactics_defaults = {
                "season_no": 23, "battle_num": 0, "win": 0, "draft_chance": 99,
                "battle_chance": 99, "is_draft": 1, "coin": 999999,
                "token": "msa-tactics-init",
                "deck": {
                    "cover": 0,
                    "unit_ids": [939, 940, 941, 944, 945,
                                 947, 949, 950, 955, 957],
                    "deck_attribute": [0] * 10,
                },
                "supporter": [1989, 1969],
                "shop": {"contents": [
                    {
                        "id": item_id,
                        "chance": 99 if item_id <= 13 else 1,
                    }
                    for item_id in range(1, 29)
                ]},
                "history": [], "reported_rooms": [],
                "claimed_time_rewards": [],
        }
        repaired_tactics = {**tactics_defaults, **tactics_data}
        if repaired_tactics != tactics_data:
            atomic_write(
                tactics_file,
                json.dumps(repaired_tactics, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8"),
            )


def load_client_json(client: str, name: str, fallback):
    target = client_data_file(client, name)
    if target.is_file():
        sentinel = object()
        result = read_json_file(target, sentinel)
        if result is not sentinel:
            return result
        if name == "user_deck.json":
            backup = client_data_file(client, "user_deck.backup.json")
            if backup.is_file():
                logging.warning("Recuperando mazos desde respaldo: %s", backup)
                return read_json_file(backup, fallback)
    # Si es un cliente nuevo y solicita datos base, asegurar placeholders completos
    ensure_client_defaults(client)
    if target.is_file():
        sentinel = object()
        result = read_json_file(target, sentinel)
        if result is not sentinel:
            return result
    # Compatibilidad con perfiles/mazos creados cuando la IP era la identidad.
    legacy_ip = _legacy_client_ips.get(client)
    if legacy_ip:
        legacy_target = client_data_file(legacy_ip, name)
        if legacy_target.is_file():
            sentinel = object()
            result = read_json_file(legacy_target, sentinel)
            if result is not sentinel:
                return result
        if name == "user_deck.json":
            legacy_backup = client_data_file(legacy_ip, "user_deck.backup.json")
            if legacy_backup.is_file():
                return read_json_file(legacy_backup, fallback)
    authoritative_default = SERVER_DEFAULTS / name
    if name in {"user_deck.json", "user_units.json"} and authoritative_default.is_file():
        return read_json_file(authoritative_default, fallback)
    return load_json(name, fallback)
