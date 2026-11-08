"""Estado efímero y concurrente del matchmaking público."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from . import moderation


class OnlineMatchmaker:
    """Emparejamiento HTTP que entrega una misma sala Photon a 2 o 4 clientes."""

    def __init__(self) -> None:
        self.condition = threading.Condition(threading.RLock())
        self.waiting: dict[str, dict | None] = {"1on1": None, "2on2": None}
        self.client_rooms: dict[tuple[str, str], dict] = {}
        self.rooms_by_id: dict[str, dict] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        banned = {account for account, record in moderation.load()["bans"].items()
                  if record["active"]}
        expired = [room_id for room_id, room in self.rooms_by_id.items()
                   if now - room.get("last_activity", room["created"]) > 600
                   or (room.get("completed") is None
                       and banned.intersection(room["clients"]))]
        for room_id in expired:
            room = self.rooms_by_id.pop(room_id)
            if self.waiting.get(room["mode"]) is room:
                self.waiting[room["mode"]] = None
            for participant in room["clients"]:
                key = (room["mode"], participant)
                if self.client_rooms.get(key) is room:
                    self.client_rooms.pop(key, None)
        if expired:
            self.condition.notify_all()

    def account_busy(self, client: str) -> bool:
        """Conservatively refuse transfers while a room may still use Photon."""
        with self.condition:
            self._prune()
            return any(client in room["clients"]
                       for room in self.rooms_by_id.values())

    def enter(self, mode: str, client: str) -> dict:
        """Asigna sala sin bloquear la transición de la pantalla de búsqueda."""
        capacity = 4 if mode == "2on2" else 2
        key = (mode, client)
        with self.condition:
            self._prune()
            room = self.client_rooms.get(key)
            if room is not None and room.get("completed") is not None:
                self.client_rooms.pop(key, None)
                room = None
            if room is None:
                room = self.waiting.setdefault(mode, None)
                if room is None or len(room["clients"]) >= capacity:
                    room = {
                        "id": f"msa-{mode}-{uuid.uuid4().hex[:12]}", "mode": mode,
                        "clients": [], "capacity": capacity,
                        "created": time.monotonic(),
                        "last_activity": time.monotonic(), "completed": None,
                        "selections": {}, "battle_started": set(),
                    }
                    self.waiting[mode] = room
                    self.rooms_by_id[room["id"]] = room
                room["clients"].append(client)
                self.client_rooms[key] = room
                logging.info("MATCHMAKING mode=%s room=%s cliente=%s slot=%d/%d",
                             mode, room["id"], client,
                             len(room["clients"]), capacity)
                if len(room["clients"]) == capacity:
                    self.waiting[mode] = None
                    self.condition.notify_all()
            room["last_activity"] = time.monotonic()
            return room

    def create_private(self, mode: str, client: str,
                       capacity: int = 2) -> dict:
        """Crea una sala dirigida que nunca entra en la cola pública."""
        key = (mode, client)
        with self.condition:
            self._prune()
            previous = self.client_rooms.get(key)
            if previous is not None and previous.get("completed") is None:
                return previous
            room = {
                "id": f"msa-{mode}-{uuid.uuid4().hex[:12]}",
                "mode": mode,
                "clients": [client],
                "capacity": max(2, capacity),
                "created": time.monotonic(),
                "last_activity": time.monotonic(),
                "completed": None,
                "selections": {},
                "battle_started": set(),
                "private": True,
                "owner": client,
            }
            self.rooms_by_id[room["id"]] = room
            self.client_rooms[key] = room
            logging.info(
                "MATCHMAKING_PRIVATE_CREATE mode=%s room=%s cliente=%s",
                mode, room["id"], client,
            )
            return room

    def room(self, mode: str, room_id: str) -> dict | None:
        """Obtiene una sala por id aunque el solicitante aún no haya entrado."""
        with self.condition:
            self._prune()
            room = self.rooms_by_id.get(room_id)
            if room is None or room.get("mode") != mode:
                return None
            room["last_activity"] = time.monotonic()
            return room

    def join_private(self, mode: str, client: str,
                     room_id: str) -> dict | None:
        """Incorpora al invitado a una sala dirigida existente."""
        with self.condition:
            room = self.room(mode, room_id)
            if room is None or not room.get("private"):
                return None
            if client not in room["clients"]:
                if len(room["clients"]) >= int(room.get("capacity", 2)):
                    return None
                room["clients"].append(client)
            self.client_rooms[(mode, client)] = room
            room["last_activity"] = time.monotonic()
            self.condition.notify_all()
            logging.info(
                "MATCHMAKING_PRIVATE_JOIN mode=%s room=%s cliente=%s slot=%d",
                mode, room_id, client, room["clients"].index(client) + 1,
            )
            return room

    def wait_until_full(self, mode: str, client: str, room_id: str,
                        timeout: float = 25.0) -> dict | None:
        """Espera una sala privada completa sin inventar participantes."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self.condition:
            while True:
                room = self.get(mode, client, room_id)
                if room is None:
                    return None
                if len(room["clients"]) >= int(room.get("capacity", 2)):
                    room["last_activity"] = time.monotonic()
                    return room
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return room
                self.condition.wait(remaining)

    def get(self, mode: str, client: str, room_id: str = "") -> dict | None:
        with self.condition:
            self._prune()
            if room_id:
                room = self.rooms_by_id.get(room_id)
                if (room is not None and room["mode"] == mode
                        and client in room["clients"]):
                    room["last_activity"] = time.monotonic()
                    return room
            return self.client_rooms.get((mode, client))

    def select_deck(self, mode: str, client: str, deck_no: int,
                    room_id: str = "", cover: int | None = None,
                    supporter_ids: list[int] | None = None) -> None:
        with self.condition:
            room = self.get(mode, client, room_id)
            if room:
                room["selections"][client] = {
                    "deck_no": max(1, min(deck_no, 26)),
                    "cover": cover if cover in (0, 1, 2) else None,
                    "supporter_ids": list((supporter_ids or [])[:3]),
                }
                room["last_activity"] = time.monotonic()

    def mark_battle_started(self, mode: str, client: str,
                            room_id: str = "") -> None:
        """Libera la búsqueda conservando la sala para peticiones tardías."""
        with self.condition:
            room = self.get(mode, client, room_id)
            if not room:
                return
            room["battle_started"].add(client)
            room["completed"] = room.get("completed") or time.monotonic()
            room["last_activity"] = time.monotonic()
            if self.waiting.get(mode) is room:
                self.waiting[mode] = None
            for participant in room["clients"]:
                key = (mode, participant)
                if self.client_rooms.get(key) is room:
                    self.client_rooms.pop(key, None)
            self.condition.notify_all()

    def leave(self, mode: str, client: str, room_id: str = "") -> None:
        with self.condition:
            room = self.get(mode, client, room_id)
            if not room:
                return
            key = (mode, client)
            if self.client_rooms.get(key) is room:
                self.client_rooms.pop(key, None)
            if room.get("completed") is None and client in room["clients"]:
                room["clients"].remove(client)
            if self.waiting.get(mode) is room and not room["clients"]:
                self.waiting[mode] = None
            room["last_activity"] = time.monotonic()
            self.condition.notify_all()
