"""Salas dirigidas y contratos HTTP de Friend Online 1on1."""

from __future__ import annotations

import logging

from .identity import public_player_id
from .matchmaking import OnlineMatchmaker
from .online import online_player
from .sessions import UserTracker
from .social import SocialController


class FriendOnlineController:
    """Coordina una sala privada entre dos amigos y su invitación de chat."""

    PREFIX = "/friend_online_1on1/"
    MODE = "friend_1on1"

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
        if action == "create_room":
            room = self.matchmaker.create_private(self.MODE, client)
            return {"room_id": room["id"], "response": ok}

        room_id = self._first(params, "room_id")
        room = self.matchmaker.room(self.MODE, room_id)

        if action == "decide_room":
            friend_id = self._first(
                params, "friend_open_id", "open_id", "open_id[]"
            )
            friend_client = (
                self.social.client_for_player(friend_id) if friend_id else ""
            )
            if room is None:
                logging.warning(
                    "FRIEND_MATCH_DECIDE invalido actor=%s friend=%s room=%s",
                    client, friend_id, room_id,
                )
                return {"type": 1, "response": ok}

            owner = str(room.get("owner", ""))
            invitee = str(room.get("invitee", ""))
            # El propietario envía friend_open_id, pero el cliente invitado
            # envía open_id[]. Si ese identificador falta en una reentrada,
            # la propia sala ya contiene al rival autorizado.
            if not friend_client:
                if client == invitee:
                    friend_client = owner
                elif client == owner and invitee:
                    friend_client = invitee
            if client == owner:
                if not friend_client or friend_client == client:
                    return {"type": 1, "response": ok}
                if not self.social.record_friend_battle_invitation(
                        client, friend_id, room_id):
                    return {"type": 1, "response": ok}
                room["invitee"] = friend_client
                room["invitee_id"] = friend_id
            elif client == invitee:
                if friend_client != owner:
                    logging.warning(
                        "FRIEND_MATCH_JOIN rechazado actor=%s friend=%s room=%s",
                        client, friend_id, room_id,
                    )
                    return {"type": 1, "response": ok}
                room = self.matchmaker.join_private(
                    self.MODE, client, room_id
                ) or room
            elif client not in room.get("clients", []):
                logging.warning(
                    "FRIEND_MATCH_JOIN rechazado actor=%s friend=%s room=%s",
                    client, friend_id, room_id,
                )
                return {"type": 1, "response": ok}

            deck_no = self._first_int(params, "deck_no", 1)
            cover = self._optional_int(params, "cover", "under_cover")
            supporters = self._int_values(params.get("supporter[]", []))
            self.matchmaker.select_deck(
                self.MODE, client, deck_no, room_id, cover, supporters
            )
            player_type = (
                room["clients"].index(client) + 1
                if client in room.get("clients", []) else 1
            )
            logging.info(
                "FRIEND_MATCH_DECIDE room=%s actor=%s friend=%s type=%d",
                room_id, client, friend_id, player_type,
            )
            return {"type": player_type, "response": ok}

        if action == "battle_start":
            room = self.matchmaker.get(self.MODE, client, room_id)
            # Tolera que el cliente salte o repita decide_room: solamente el
            # invitado que el propietario dejó registrado puede auto-unirse.
            if room is None:
                candidate = self.matchmaker.room(self.MODE, room_id)
                if candidate is not None and client == candidate.get("invitee"):
                    room = self.matchmaker.join_private(
                        self.MODE, client, room_id
                    )
            clients = list(room.get("clients", [])) if room else [client]
            invitee = str(room.get("invitee", "")) if room else ""
            if invitee and invitee not in clients and len(clients) < 2:
                clients.append(invitee)
            while len(clients) < 2:
                clients.append(f"pending-{len(clients) + 1}")
            players = []
            for slot, participant in enumerate(clients[:2]):
                selection = (
                    room.get("selections", {}).get(participant, {})
                    if room else {}
                )
                players.append(online_player(
                    public_player_id(participant), slot, participant,
                    int(selection.get("deck_no", 1)),
                    selection.get("cover"),
                    selection.get("supporter_ids", []),
                ))
            active_room_id = room["id"] if room else room_id
            if self.tracker is not None and room is not None:
                self.tracker.record_pvp_battle_start(
                    self.MODE, active_room_id,
                    [value for value in clients
                     if not value.startswith("pending-")],
                    source_ip,
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
