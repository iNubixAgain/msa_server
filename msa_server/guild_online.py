"""Salas privadas de Guild Online 2on2 y su contrato de chat."""

from __future__ import annotations

import logging

from .guild import guild_id_for_client
from .identity import public_player_id
from .matchmaking import OnlineMatchmaker
from .online import online_player
from .sessions import UserTracker
from .social import SocialController


class GuildOnlineController:
    """Reune cuatro miembros del mismo Guild antes de iniciar la batalla."""

    PREFIX = "/guild_online_2on2/"
    MODE = "guild_2on2"
    CAPACITY = 4
    WAIT_SECONDS = 25.0

    def __init__(self, matchmaker: OnlineMatchmaker,
                 social: SocialController,
                 tracker: UserTracker | None = None) -> None:
        self.matchmaker = matchmaker
        self.social = social
        self.tracker = tracker

    def handles(self, path: str) -> bool:
        return path.startswith(self.PREFIX)

    def handle(self, path: str, client: str,
               params: dict[str, list[str]], source_ip: str,
               ok: dict) -> dict | None:
        if not self.handles(path):
            return None
        action = path.rsplit("/", 1)[-1]
        guild_id = guild_id_for_client(client)
        if guild_id <= 0:
            logging.warning("GUILD_2ON2 rechazado actor=%s sin guild", client)
            return self._error(ok, 403)

        if action == "create_room":
            room = self.matchmaker.create_private(
                self.MODE, client, capacity=self.CAPACITY
            )
            room["guild_id"] = guild_id
            if not self.social.record_guild_battle_invitation(
                    client, guild_id, room["id"]):
                self.matchmaker.leave(self.MODE, client, room["id"])
                return self._error(ok, 403)
            return {"room_id": room["id"], "response": ok}

        room_id = self._first(params, "room_id")
        room = self.matchmaker.room(self.MODE, room_id)
        if room is None or int(room.get("guild_id", 0)) != guild_id:
            logging.warning(
                "GUILD_2ON2 sala rechazada actor=%s guild=%s room=%s",
                client, guild_id, room_id,
            )
            return self._error(ok, 403)

        if action == "decide_room":
            if client not in room.get("clients", []):
                room = self.matchmaker.join_private(
                    self.MODE, client, room_id
                )
            if room is None or client not in room.get("clients", []):
                return self._error(ok, 409)
            deck_no = self._first_int(params, "deck_no", 1)
            cover = self._optional_int(params, "cover", "under_cover")
            supporters = self._int_values(params.get("supporter[]", []))
            self.matchmaker.select_deck(
                self.MODE, client, deck_no, room_id, cover, supporters
            )
            player_type = room["clients"].index(client) + 1
            logging.info(
                "GUILD_2ON2_DECIDE room=%s actor=%s slot=%d/%d",
                room_id, client, player_type, self.CAPACITY,
            )
            return {"type": player_type, "response": ok}

        if action == "battle_start":
            if client not in room.get("clients", []):
                return self._error(ok, 403)
            room = self.matchmaker.wait_until_full(
                self.MODE, client, room_id, self.WAIT_SECONDS
            )
            clients = list(room.get("clients", [])) if room else []
            if len(clients) != self.CAPACITY:
                logging.info(
                    "GUILD_2ON2_WAIT room=%s actor=%s jugadores=%d/%d",
                    room_id, client, len(clients), self.CAPACITY,
                )
                return self._error(ok, 408, waiting=True,
                                   joined=len(clients))
            players = []
            for slot, participant in enumerate(clients):
                selection = room.get("selections", {}).get(participant, {})
                players.append(online_player(
                    public_player_id(participant), slot, participant,
                    int(selection.get("deck_no", 1)),
                    selection.get("cover"),
                    selection.get("supporter_ids", []),
                ))
            if self.tracker is not None:
                self.tracker.record_pvp_battle_start(
                    self.MODE, room_id, clients, source_ip
                )
            self.matchmaker.mark_battle_started(
                self.MODE, client, room_id
            )
            return {"player_info": players, "response": ok}

        if action == "battle_end":
            if self.tracker is not None:
                self.tracker.record_pvp_battle_end(
                    self.MODE, room_id, client
                )
            self.matchmaker.mark_battle_started(
                self.MODE, client, room_id
            )
            return {"response": ok}

        if action == "leave_room":
            if self.tracker is not None:
                self.tracker.record_pvp_battle_end(
                    self.MODE, room_id, client
                )
            self.matchmaker.leave(self.MODE, client, room_id)
            return {"response": ok}
        return None

    @staticmethod
    def _error(ok: dict, code: int, **extra) -> dict:
        response = dict(ok)
        response["error_code"] = code
        return {**extra, "response": response}

    @staticmethod
    def _first(params: dict[str, list[str]], *names: str) -> str:
        for name in names:
            values = params.get(name)
            if values:
                return str(values[0]).strip()
        return ""

    @classmethod
    def _first_int(cls, params: dict[str, list[str]], name: str,
                   default: int) -> int:
        try:
            return int(cls._first(params, name) or default)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _optional_int(cls, params: dict[str, list[str]],
                      *names: str) -> int | None:
        try:
            value = cls._first(params, *names)
            return int(value) if value else None
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
