"""Contratos HTTP, persistencia y construcción de participantes y récord de los modos Online."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from . import storage
from .identity import public_player_id
from .matchmaking import OnlineMatchmaker
from .profiles import get_profile, normalise_profile, profile_detail
from .sessions import UserTracker
from .storage import (
    atomic_write,
    client_data_file,
    client_lock,
    load_client_json,
    read_json_file,
)
from .users import user_payload
from .inventory import owned_units_payload
from .decks import load_decks
from .pvp_history import battle_record, normalise_history

MAX_HISTORY_PER_USER = 5
WIN_RANK_POINTS = 500
ONLINE_RANKING_LIMIT = 50


def load_online_data(client: str) -> dict:
    with client_lock(client):
        data = load_client_json(client, "online.json", {})
        if not isinstance(data, dict):
            data = {}
        return {
            "online_score": int(data.get("online_score", 0)),
            "1on1_win": int(data.get("1on1_win", 0)),
            "1on1_lose": int(data.get("1on1_lose", 0)),
            "2on2_win": int(data.get("2on2_win", 0)),
            "2on2_lose": int(data.get("2on2_lose", 0)),
            "history_1on1": normalise_history(data.get("history_1on1", [])),
            "history_2on2": normalise_history(data.get("history_2on2", [])),
        }


def save_online_data(client: str, data: dict) -> None:
    with client_lock(client):
        atomic_write(
            client_data_file(client, "online.json"),
            json.dumps(data, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8"),
        )


def get_online_stats(client: str) -> dict:
    return load_online_data(client)


def online_person(client: str, default_name: str = "user",
                  score_override: int | None = None) -> dict:
    profile = get_profile(client, default_name)
    stats = get_online_stats(client)
    current_score = score_override if score_override is not None else stats["online_score"]
    return {
        "user": public_player_id(client),
        "name": profile["name"],
        "level": 50,
        "avatar_id": profile["avatar"],
        "online_score": current_score,
        "type": 1,
        "detail": profile_detail(profile),
    }


def ranking_person_from_directory(directory: Path,
                                  score_override: int | None = None) -> dict:
    """Reconstruye Person desde archivos existentes, incluso los pre-ranking."""
    profile = normalise_profile(
        read_json_file(directory / "profile.json", {}),
        f"PLAYER {directory.name[:6]}",
    )
    if score_override is None:
        saved_online = read_json_file(directory / "online.json", {})
        try:
            score = max(0, int(saved_online.get("online_score", 0)))
        except (AttributeError, TypeError, ValueError):
            score = 0
    else:
        score = max(0, int(score_override))
    return {
        # client_data_file y public_player_id usan el mismo SHA-256 de 16
        # caracteres. Esto permite recuperar el ID publico sin guardar ni
        # exponer la identidad interna client-*.
        "user": f"msa-{directory.name}",
        "name": profile["name"],
        "level": 50,
        "avatar_id": profile["avatar"],
        "online_score": score,
        "type": 1,
        "detail": profile_detail(profile),
    }


def online_ranking_records() -> list[dict]:
    """Calcula posiciones globales deterministas sobre todas las cuentas."""
    records = []
    root = storage.DATA / "clients"
    if not root.is_dir():
        return records
    for path in root.glob("*/online.json"):
        directory = path.parent
        if (len(directory.name) != 16
                or any(char not in "0123456789abcdef" for char in directory.name)):
            continue
        saved = read_json_file(path, {})
        try:
            score = max(0, int(saved.get("online_score", 0)))
        except (AttributeError, TypeError, ValueError):
            score = 0
        records.append({
            "person": ranking_person_from_directory(directory, score),
            "score": score,
        })
    records.sort(key=lambda item: (-item["score"], item["person"]["user"]))
    for rank, record in enumerate(records, 1):
        record["rank"] = rank
    return records


def online_rank(client: str, records: list[dict] | None = None) -> int:
    public_id = public_player_id(client)
    candidates = records if records is not None else online_ranking_records()
    return next(
        (record["rank"] for record in candidates
         if record["person"]["user"] == public_id),
        0,
    )


def record_online_battle(mode: str, client: str, room: dict | None,
                         battle_result: int, battle_log: str,
                         source_ip: str = "") -> int:
    """Registra el fin de batalla, actualiza puntajes y añade el récord con límite FIFO."""
    history_key = f"history_{mode}"
    win_key = f"{mode}_win"
    lose_key = f"{mode}_lose"

    # Determinar si el solicitante ganó
    is_winner = (battle_result == 1)
    add_score = WIN_RANK_POINTS if is_winner else 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Identificar oponentes
    clients_in_room = room.get("clients", []) if isinstance(room, dict) else []
    opponents = [c for c in clients_in_room if c != client and not c.startswith("pending-")]
    opponent_client = opponents[0] if opponents else "npc-rival"

    with client_lock(client):
        data = load_online_data(client)
        data["online_score"] = max(0, data["online_score"] + add_score)
        if is_winner:
            data[win_key] += 1
        elif battle_result == 2:
            data[lose_key] += 1

        persons = [
            online_person(client, f"PLAYER {source_ip or '1'}", score_override=data["online_score"]),
            online_person(opponent_client, "RIVAL"),
        ]
        if mode == "2on2":
            p3 = clients_in_room[2] if len(clients_in_room) > 2 else "npc-3"
            p4 = clients_in_room[3] if len(clients_in_room) > 3 else "npc-4"
            persons.extend((online_person(p3, "ALLY 2"), online_person(p4, "RIVAL 2")))
        record = battle_record(persons, battle_log, battle_result, now_str)

        history = data.get(history_key, [])
        history.insert(0, record)
        data[history_key] = history[:MAX_HISTORY_PER_USER]
        save_online_data(client, data)

    return add_score


def get_online_history(mode: str, client: str) -> list[dict]:
    data = load_online_data(client)
    return data.get(f"history_{mode}", [])[:MAX_HISTORY_PER_USER]


def online_player(identifier: str, slot: int, client: str,
                  deck_no: int = 1, cover: int | None = None,
                  supporter_ids: list[int] | None = None) -> dict:
    profile = get_profile(client, f"PLAYER {slot + 1}")
    stats = get_online_stats(client)
    decks = load_decks(client)
    deck = decks[deck_no - 1] if len(decks) >= deck_no else {}
    if not isinstance(deck, dict):
        deck = {}
    unit_ids = deck.get("unit_ids", [])
    if not isinstance(unit_ids, list):
        unit_ids = []
    raw_attributes = deck.get("deck_attribute", [])
    if not isinstance(raw_attributes, list):
        raw_attributes = []
    units = owned_units_payload(client).get("unit", [])
    by_id = {entry.get("param", {}).get("unit_id"): entry.get("param", {})
             for entry in units}
    # El cliente 7.13.0 recorre siempre las diez posiciones del mazo rival.
    # Las casillas vacias son UnitParam validos con unit_id=-1; eliminarlas
    # acorta el array y hace que Rank Match lea fuera de sus limites al abrir
    # la pantalla previa a la batalla.
    deck_slots = []
    for index in range(10):
        unit_id = unit_ids[index] if index < len(unit_ids) else -1
        try:
            unit_id = int(unit_id)
        except (TypeError, ValueError):
            unit_id = -1
        attribute = raw_attributes[index] if index < len(raw_attributes) else 0
        try:
            attribute = int(attribute)
        except (TypeError, ValueError):
            attribute = 0
        unit = by_id.get(unit_id)
        if not isinstance(unit, dict):
            unit = {
                "unit_id": -1, "level": 0, "evolution": 0, "version": 0,
                "equip": [0] * 6, "skill": [0] * 4,
                "status_seed": [0] * 9,
            }
        deck_slots.append((unit, attribute))
    unit_param = [unit for unit, _attribute in deck_slots]
    deck_attribute = [attribute for _unit, attribute in deck_slots]
    deployable = [
        unit for unit, attribute in deck_slots
        if unit["unit_id"] != -1 and attribute == 0
    ]
    supporter_param = [by_id[unit_id] for unit_id in (supporter_ids or [])
                       if unit_id in by_id]
    selected_cover = cover if cover in (0, 1, 2) else deck.get("cover", 0)
    return {
        "person": {
            "user": identifier,
            "name": profile["name"],
            "level": 50,
            "avatar_id": profile["avatar"],
            "online_score": stats["online_score"],
            "type": 1,
            "detail": profile_detail(profile),
        },
        "online_score": stats["online_score"], "cover": selected_cover,
        "1on1_win": stats["1on1_win"], "2on2_win": stats["2on2_win"],
        "rank": 1, "unit_param": unit_param, "deck_attribute": deck_attribute,
        "supporter_param": supporter_param,
        "soldier_unit_param": (deployable or unit_param)[0] if unit_param else {
            "unit_id": 16, "level": 50, "evolution": 5, "version": 5,
            "equip": [1] * 6, "skill": [50] * 4, "status_seed": [0] * 9,
        },
        "build_up": [50] * 11,
        "customize_affiliation": [
            {"type": 1, "level": [50] * 6},
            {"type": 2, "level": [50] * 6},
            {"type": 3, "level": [50] * 6},
        ],
        "base_avatar": profile["base_avatar"],
    }


class OnlineController:
    """Despacha acciones Online sin acoplarlas al transporte HTTP."""

    prefixes = ("/online_1on1/", "/online_2on2/")

    def __init__(self, matchmaker: OnlineMatchmaker,
                 tracker: UserTracker | None = None) -> None:
        self.matchmaker = matchmaker
        self.tracker = tracker

    def handles(self, path: str) -> bool:
        return (path == "/ranking/get_online_ranking"
                or path == "/online_1on1/top"
                or path.startswith(self.prefixes))

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               source_ip: str, ok: dict) -> dict | None:
        if path == "/ranking/get_online_ranking":
            records = online_ranking_records()
            stats = get_online_stats(client)
            return {
                "ranking_data": {
                    "my_rank": online_rank(client, records),
                    "score": stats["online_score"],
                    "top50ranking": [
                        {"rank": record["rank"], "person": record["person"]}
                        for record in records[:ONLINE_RANKING_LIMIT]
                    ],
                },
                "user": user_payload(client=client),
                "response": ok,
            }

        if path in ("/online_1on1/top", "/online_2on2/top"):
            stats = get_online_stats(client)
            return {
                "user": user_payload(
                    public_player_id(client), f"PLAYER {source_ip}", client
                ),
                "online": {
                    "1on1_win": stats["1on1_win"],
                    "1on1_lose": stats["1on1_lose"],
                    "2on2_win": stats["2on2_win"],
                    "2on2_lose": stats["2on2_lose"],
                    "my_rank": online_rank(client),
                },
                "is_stint": 0, "response": ok,
            }

        mode = "1on1" if path.startswith("/online_1on1/") else "2on2"
        action = path.rsplit("/", 1)[-1]
        if action == "create_or_enter_room":
            room = self.matchmaker.enter(mode, client)
            return {"room_id": room["id"], "response": ok}

        room_id = params.get("room_id", [""])[0]
        room = self.matchmaker.get(mode, client, room_id)
        if action == "decide_room":
            deck_no = self._first_int(params, "deck_no", 1)
            cover = self._optional_int(params, "cover")
            supporter_ids = self._int_values(params.get("supporter[]", []))
            self.matchmaker.select_deck(
                mode, client, deck_no, room_id, cover, supporter_ids
            )
            logging.info(
                "SUPPORT_SELECT mode=%s room=%s cliente=%s deck=%d cover=%s supporters=%s",
                mode, room_id or "sin-sala", client, deck_no, cover,
                supporter_ids,
            )
            room_type = 1
            if room and client in room["clients"]:
                room_type = room["clients"].index(client) + 1
            return {"type": room_type, "response": ok}

        if action == "battle_start":
            capacity = 2 if mode == "1on1" else 4
            clients = list(room["clients"]) if room else [client]
            while len(clients) < capacity:
                clients.append(f"pending-{len(clients) + 1}")
            players = []
            for slot, value in enumerate(clients[:capacity]):
                selection = (room.get("selections", {}).get(value, {})
                             if room else {})
                if isinstance(selection, int):
                    selection = {"deck_no": selection}
                players.append(online_player(
                    public_player_id(value), slot, value,
                    selection.get("deck_no", 1),
                    selection.get("cover"),
                    selection.get("supporter_ids", []),
                ))
            active_room_id = room["id"] if room else room_id or "sin-sala"
            logging.info("BATTLE_START mode=%s room=%s solicitante=%s jugadores=%s",
                          mode, active_room_id, client, clients)
            self.matchmaker.mark_battle_started(mode, client, room_id)
            if self.tracker is not None:
                real_clients = [c for c in clients if not c.startswith("pending-")]
                self.tracker.record_pvp_battle_start(mode, active_room_id, real_clients, source_ip)
            return {"player_info": players, "response": ok}

        if action == "battle_end":
            battle_result = self._first_int(params, "battle_result", 1)
            battle_log = params.get("battle_log", [""])[0]
            add_score = record_online_battle(
                mode, client, room, battle_result, battle_log, source_ip
            )
            self.matchmaker.mark_battle_started(mode, client, room_id)
            if self.tracker is not None:
                active_room_id = room["id"] if room else room_id
                self.tracker.record_pvp_battle_end(mode, active_room_id, client)

            stats = get_online_stats(client)
            return {
                "user": user_payload(client=client),
                "online": {
                    "1on1_win": stats["1on1_win"],
                    "1on1_lose": stats["1on1_lose"],
                    "2on2_win": stats["2on2_win"],
                    "2on2_lose": stats["2on2_lose"],
                    "my_rank": online_rank(client),
                },
                "reward": [],
                "reward_win5": [],
                "add_online_score": add_score,
                "is_stint": 0,
                "response": ok,
            }

        if action == "history":
            history = get_online_history(mode, client)
            return {"online_battle_result": history, "response": ok}

        if action in {"leave_room", "report"}:
            self.matchmaker.leave(mode, client, room_id)
            if self.tracker is not None and action == "leave_room":
                active_room_id = room["id"] if room else room_id
                self.tracker.record_pvp_battle_end(mode, active_room_id, client)
            return {"response": ok}
        return None

    @staticmethod
    def _first_int(params: dict[str, list[str]], key: str,
                   default: int) -> int:
        try:
            return int(params.get(key, [str(default)])[0])
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _optional_int(params: dict[str, list[str]], key: str) -> int | None:
        try:
            return int(params.get(key, [""])[0])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _int_values(values: list[str]) -> list[int]:
        result = []
        for value in values:
            try:
                result.append(int(value))
            except (TypeError, ValueError):
                continue
        return result
