"""Rank Match (Arena): matchmaking, progreso competitivo y persistencia."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from typing import Mapping

from . import storage
from .identity import public_player_id
from .matchmaking import OnlineMatchmaker
from .online import online_person, online_player, ranking_person_from_directory
from .pvp_history import battle_record, normalise_history
from .sessions import UserTracker
from .storage import (
    atomic_write,
    client_data_file,
    client_lock,
    load_client_json,
    read_json_file,
)
from .users import user_payload


ARENA_SEASON = 58
MAX_HISTORY = 5
WIN_RATING = 10
LOSE_RATING = -5
WIN_STREAK_BONUS_AT = 3
WIN_STREAK_BONUS_RATING = 5

# El orden sigue la presentación del cliente: III -> II -> I.
ARENA_LADDER = tuple(
    (rank, arena_class)
    for rank in range(1, 5)
    for arena_class in (3, 2, 1)
) + ((5, 0),)
STARS_REQUIRED = {1: 3, 2: 4, 3: 5, 4: 6, 5: 0}

_result_lock = threading.RLock()


def default_arena() -> dict:
    return {
        "season_no": ARENA_SEASON,
        "rank": 1,
        "class": 3,
        "order": 0,
        "rating": 0,
        "token": f"msa-arena-{uuid.uuid4().hex[:16]}",
        "win": 0,
        "win3": 0,
        "win_beat": 0,
        "best_rank": 1,
        "best_class": 3,
        "penalty_time": "2000-01-01 00:00:00",
        "penalty_rating": 0,
        "penalty_reason": 0,
        "cheat_times": 0,
        "battle_win": 0,
        "battle_lose": 0,
        "history": [],
        "honor_history": [],
        "reported_rooms": [],
    }


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalise_arena(saved) -> dict:
    base = default_arena()
    if isinstance(saved, dict):
        base.update(saved)
    base["season_no"] = ARENA_SEASON
    base["rank"] = _int(base.get("rank"), 1)
    base["class"] = _int(base.get("class"), 3)
    if (base["rank"], base["class"]) not in ARENA_LADDER:
        base["rank"], base["class"] = 1, 3
    for key in (
        "order", "rating", "win", "win3", "win_beat", "best_rank",
        "best_class", "penalty_rating", "penalty_reason", "cheat_times",
        "battle_win", "battle_lose",
    ):
        base[key] = max(0, _int(base.get(key), 0))
    base["history"] = normalise_history(base.get("history", []), MAX_HISTORY)
    base["honor_history"] = list(base.get("honor_history", []))[:20]
    base["reported_rooms"] = list(base.get("reported_rooms", []))[-20:]
    if not isinstance(base.get("token"), str) or not base["token"]:
        base["token"] = f"msa-arena-{uuid.uuid4().hex[:16]}"
    return base


def load_arena(client: str) -> dict:
    with client_lock(client):
        saved = load_client_json(client, "arena.json", None)
        state = _normalise_arena(saved)
        if state != saved:
            save_arena(client, state)
        return state


def save_arena(client: str, state: dict) -> None:
    with client_lock(client):
        state = _normalise_arena(state)
        state["public_id"] = public_player_id(client)
        person = online_person(client)
        state["player_snapshot"] = person
        atomic_write(
            client_data_file(client, "arena.json"),
            json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )


def arena_payload(state: dict) -> dict:
    keys = (
        "season_no", "rank", "class", "order", "rating", "token", "win",
        "win3", "win_beat", "best_rank", "best_class", "penalty_time",
        "penalty_rating", "penalty_reason", "cheat_times",
    )
    return {key: state[key] for key in keys}


def _ladder_index(state: dict) -> int:
    return ARENA_LADDER.index((state["rank"], state["class"]))


def _update_best(state: dict) -> None:
    current = _ladder_index(state)
    try:
        best = ARENA_LADDER.index((state["best_rank"], state["best_class"]))
    except ValueError:
        best = 0
    if current > best:
        state["best_rank"], state["best_class"] = ARENA_LADDER[current]


def apply_result(state: dict, won: bool) -> tuple[int, bool, bool]:
    """Aplica estrellas/rating sin bajar de III en la categoría alcanzada."""
    index = _ladder_index(state)
    changed_level = False
    won_third = False
    if won:
        state["battle_win"] += 1
        state["win"] += 1
        state["win_beat"] += 1
        state["win3"] += 1
        delta = WIN_RATING
        if state["win_beat"] >= WIN_STREAK_BONUS_AT:
            delta += WIN_STREAK_BONUS_RATING
        if state["win3"] >= 3:
            state["win3"] = 0
            won_third = True
        required = STARS_REQUIRED[state["rank"]]
        if required and state["win"] >= required and index < len(ARENA_LADDER) - 1:
            index += 1
            state["rank"], state["class"] = ARENA_LADDER[index]
            state["win"] = 0
            changed_level = True
            _update_best(state)
    else:
        state["battle_lose"] += 1
        state["win_beat"] = 0
        delta = LOSE_RATING
        if state["win"] > 0:
            state["win"] -= 1
        # III es el piso de la categoría actual. Desde II o I se baja una
        # división y se queda a una estrella del ascenso; desde III, pérdidas
        # adicionales solo afectan rating y estadísticas. El rango especial
        # final (clase 0) conserva la transición nativa al nivel anterior.
        elif index > 0 and state["class"] != 3:
            index -= 1
            state["rank"], state["class"] = ARENA_LADDER[index]
            state["win"] = max(0, STARS_REQUIRED[state["rank"]] - 1)
            changed_level = True
    state["rating"] = max(0, state["rating"] + delta)
    return delta, changed_level, won_third


def arena_player(client: str, slot: int, room: dict | None = None) -> dict:
    selection = room.get("selections", {}).get(client, {}) if room else {}
    if isinstance(selection, int):
        selection = {"deck_no": selection}
    player = online_player(
        public_player_id(client), slot, client,
        selection.get("deck_no", 1), selection.get("cover"),
        selection.get("supporter_ids", []),
    )
    state = load_arena(client)
    return {
        # El contrato nativo de Arena llama a este objeto ``info``. Los APK
        # ARM64 anteriores al arreglo del slot del endpoint perdieron esa
        # cadena y terminan consultando la clave vacia; conservar el alias
        # permite que esas instalaciones completen la partida sin reinstalar.
        "info": player,
        "": player,
        "order": state["order"],
        "rating": state["rating"],
        "rank": state["rank"],
        "class": state["class"],
        "win": state["win"],
        "win_beat": state["win_beat"],
    }


def _ranking_records() -> list[dict]:
    records = []
    root = storage.DATA / "clients"
    if not root.is_dir():
        return records
    for path in root.glob("*/arena.json"):
        state = _normalise_arena(read_json_file(path, {}))
        directory = path.parent
        if (len(directory.name) != 16
                or any(char not in "0123456789abcdef" for char in directory.name)):
            continue
        person = ranking_person_from_directory(directory)
        records.append({"rating": state["rating"], "person": person})
    records.sort(key=lambda item: (-item["rating"], item["person"]["user"]))
    for order, record in enumerate(records, 1):
        record["order"] = order
    return records


class ArenaController:
    """Atiende el contrato nativo de Rank Match bajo `/arena/`."""

    prefix = "/arena/"

    def __init__(self, matchmaker: OnlineMatchmaker,
                 tracker: UserTracker | None = None) -> None:
        self.matchmaker = matchmaker
        self.tracker = tracker

    def handles(self, path: str) -> bool:
        return path == "/arena/top" or path.startswith(self.prefix)

    def handle(self, path: str, client: str, params: Mapping[str, list[str]],
               source_ip: str, ok: dict) -> dict | None:
        if not self.handles(path):
            return None
        action = path.rstrip("/").rsplit("/", 1)[-1]
        state = load_arena(client)

        if action == "top":
            return {
                "user": user_payload(client=client),
                "arena": arena_payload(state),
                "response": ok,
            }

        if action == "create_or_enter_room":
            room = self.matchmaker.enter("arena", client)
            state["token"] = f"msa-arena-{uuid.uuid4().hex[:16]}"
            save_arena(client, state)
            return {"room_id": room["id"], "matching": 1, "response": ok}

        room_id = params.get("room_id", [""])[0]
        room = self.matchmaker.get("arena", client, room_id)

        if action == "decide_room":
            deck_no = self._first_int(params, ("favorite_deck_no", "deck_no"), 1)
            cover = self._optional_int(params, ("under_cover", "cover"))
            supporters = self._int_values(
                params.get("supporter[]", params.get("supporter_id[]", []))
            )
            self.matchmaker.select_deck(
                "arena", client, deck_no, room_id, cover, supporters
            )
            logging.info(
                "ARENA_DECK_SELECT room=%s cliente=%s deck=%d cover=%s supporters=%s",
                room_id or "sin-sala", client, deck_no, cover, supporters,
            )
            player_no = 1
            if room and client in room["clients"]:
                player_no = room["clients"].index(client) + 1
            return {"type": player_no, "response": ok}

        if action == "battle_start":
            clients = list(room["clients"]) if room else [client]
            while len(clients) < 2:
                clients.append("arena-npc-rival")
            players = [arena_player(value, slot, room) for slot, value in enumerate(clients[:2])]
            active_room = room["id"] if room else room_id or "sin-sala"
            self.matchmaker.mark_battle_started("arena", client, room_id)
            if self.tracker is not None:
                real = [value for value in clients[:2] if value != "arena-npc-rival"]
                self.tracker.record_pvp_battle_start("rank", active_room, real, source_ip)
            return {
                "player_info": players,
                "beat_rating": WIN_STREAK_BONUS_RATING,
                "win_rating": WIN_RATING,
                "lose_rating": LOSE_RATING,
                "response": ok,
            }

        if action == "battle_end":
            battle_result = self._first_int(params, ("battle_result", "result"), 1)
            won = battle_result == 1
            with _result_lock:
                state = load_arena(client)
                duplicate = bool(room_id and room_id in state["reported_rooms"])
                if duplicate:
                    delta, changed_level, won_third = 0, False, False
                else:
                    delta, changed_level, won_third = apply_result(state, won)
                    if room_id:
                        state["reported_rooms"].append(room_id)
                        state["reported_rooms"] = state["reported_rooms"][-20:]
                    self._record_history(state, client, room, battle_result, params)
                    save_arena(client, state)
            if self.tracker is not None:
                self.tracker.record_pvp_battle_end("rank", room_id, client)
            return {
                "user": user_payload(client=client),
                "arena": arena_payload(state),
                "reward": [],
                "reward_class": [] if not changed_level else [{"item_id": 1, "num": 1000}],
                "reward_win3": [{"item_id": 1, "num": 5}] if won_third else [],
                "result_type": battle_result,
                "add_rating": delta,
                "response": ok,
            }

        if action == "leave_room":
            self.matchmaker.leave("arena", client, room_id)
            return {"response": ok}

        if action == "battle_history":
            return {"online_battle_result": state["history"][:MAX_HISTORY], "response": ok}

        if action == "ranking_top":
            records = _ranking_records()
            own = next((entry["order"] for entry in records
                        if entry["person"].get("user") == public_player_id(client)), 0)
            state["order"] = own
            save_arena(client, state)
            return {
                "ranking": {
                    "season_no": ARENA_SEASON,
                    "rank": state["rank"], "class": state["class"],
                    "order": own, "rating": state["rating"], "record": records[:100],
                },
                "response": ok,
            }

        if action == "honor_history":
            return {
                "arena_honor_history": {
                    "record": state["honor_history"],
                    "best_record": {
                        "season_no": ARENA_SEASON,
                        "rank": state["best_rank"], "class": state["best_class"],
                        "order": state["order"], "rating": state["rating"],
                    },
                },
                "response": ok,
            }
        return None

    @staticmethod
    def _record_history(state: dict, client: str, room: dict | None,
                        result: int, params: Mapping[str, list[str]]) -> None:
        clients = room.get("clients", []) if room else []
        opponent = next((value for value in clients if value != client), "arena-npc-rival")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record = battle_record(
            (online_person(client), online_person(opponent, "RANK RIVAL")),
            params.get("battle_log", [""])[0], result, timestamp,
        )
        state["history"].insert(0, record)
        state["history"] = state["history"][:MAX_HISTORY]

    @staticmethod
    def _first_int(params: Mapping[str, list[str]], keys: tuple[str, ...],
                   default: int) -> int:
        for key in keys:
            values = params.get(key)
            if not values:
                continue
            try:
                return int(values[0])
            except (TypeError, ValueError):
                continue
        return default

    @staticmethod
    def _optional_int(params: Mapping[str, list[str]], keys: tuple[str, ...]) -> int | None:
        for key in keys:
            try:
                return int(params.get(key, [""])[0])
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _int_values(values: list[str]) -> list[int]:
        result = []
        for value in values:
            for part in str(value).split(","):
                try:
                    result.append(int(part))
                except (TypeError, ValueError):
                    continue
        return result[:3]
