"""Notificaciones dentro del juego usando el buzon nativo de MSA."""

from __future__ import annotations

import json
import threading
from typing import Iterable

from . import storage
from .guild import guild_id_for_client
from .identity import public_player_id
from .storage import atomic_write, client_data_file, read_json_file
from .users import user_payload


_LOCK = threading.RLock()
_MAX_NOTIFICATIONS = 50


def _messages(document, key: str = "messages") -> list[dict]:
    values = document.get(key, []) if isinstance(document, dict) else []
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _state(client: str) -> dict:
    path = client_data_file(client, "ingame_notifications.json")
    document = read_json_file(path, {}) if path.is_file() else {}
    read_ids = document.get("read_ids", []) if isinstance(document, dict) else []
    return {
        "version": 1,
        "read_ids": list(dict.fromkeys(
            str(value) for value in read_ids if str(value)
        ))[-200:],
    }


def _save_state(client: str, state: dict) -> None:
    atomic_write(
        client_data_file(client, "ingame_notifications.json"),
        json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        .encode("utf-8"),
    )


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _subject(message: dict) -> str:
    event = str(message.get("system_event", ""))
    body = str(message.get("message", ""))
    if event == "friend_request" or body == "FRIEND REQUEST":
        return "Nueva solicitud de amistad"
    if event == "friend_online_1on1":
        return "Invitacion a Friend Battle"
    if event in {"guild_online_1on1", "guild_online_2on2"}:
        return "Invitacion a Guild Battle"
    if event == "guild_join":
        return "Actividad del Guild"
    if _safe_int(message.get("message_type"), 1) == 2:
        return "Nuevo mensaje del Guild"
    return "Nuevo mensaje privado"


class InGameNotificationController:
    """Convierte eventos sociales persistidos en entradas del buzon nativo."""

    ROUTES = {"/mail/top", "/mail/get_present"}

    def handles(self, path: str) -> bool:
        return path in self.ROUTES

    @staticmethod
    def _visible_messages(client: str) -> list[dict]:
        player_id = public_player_id(client)
        social = read_json_file(storage.DATA / "social.json", {})
        blacklists = social.get("blacklists", {}) if isinstance(social, dict) else {}
        blocked_values = blacklists.get(player_id, []) if isinstance(blacklists, dict) else []
        blocked = {str(value) for value in blocked_values}

        private_doc = read_json_file(storage.DATA / "chat_private.json", {})
        visible = [
            value for value in _messages(private_doc)
            if str(value.get("recipient", "")) == player_id
            and str(value.get("sender", "")) not in {"", player_id}
            and str(value.get("sender", "")) not in blocked
        ]

        guild_id = guild_id_for_client(client)
        if guild_id > 0:
            guild_doc = read_json_file(storage.DATA / "chat_guild.json", {})
            guilds = guild_doc.get("guilds", {}) if isinstance(guild_doc, dict) else {}
            guild_messages: Iterable = (
                guilds.get(str(guild_id), []) if isinstance(guilds, dict) else []
            )
            visible.extend(
                value for value in guild_messages
                if isinstance(value, dict)
                and str(value.get("sender", "")) not in {"", player_id}
                and str(value.get("sender", "")) not in blocked
            )

        visible.sort(key=lambda value: (
            _safe_int(value.get("timestamp")), str(value.get("id", ""))
        ), reverse=True)
        return visible[:_MAX_NOTIFICATIONS]

    @staticmethod
    def _entry(message: dict, unread: bool) -> dict:
        message_id = str(message.get("id", ""))
        return {
            "id": message_id,
            "timestamp": _safe_int(message.get("timestamp")),
            "datetime": str(message.get("datetime", "")),
            "img": "",
            "subject": _subject(message),
            "body": str(message.get("message", ""))[:256],
            "present_id": 0,
            "unread": 1 if unread else 0,
        }

    def status(self, client: str) -> dict:
        with _LOCK:
            visible = self._visible_messages(client)
            state = _state(client)
            read_ids = set(state["read_ids"])
            unread = [
                message for message in visible
                if str(message.get("id", "")) not in read_ids
            ]
            latest = str(visible[0].get("datetime", "")) if visible else ""
            return {"unread_mail": len(unread), "latest_date": latest}

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               ok: dict) -> dict | None:
        if not self.handles(path):
            return None
        with _LOCK:
            visible = self._visible_messages(client)
            state = _state(client)
            read_ids = set(state["read_ids"])
            if path == "/mail/top":
                entries = [
                    self._entry(message, str(message.get("id", "")) not in read_ids)
                    for message in visible
                ]
                state["read_ids"] = list(dict.fromkeys(
                    state["read_ids"]
                    + [str(message.get("id", "")) for message in visible]
                ))[-200:]
                _save_state(client, state)
                return {
                    "user": user_payload(client=client),
                    "mail_box": entries,
                    "response": ok,
                }

            # Las notificaciones informativas no incluyen recompensas. Se
            # conserva el contrato por si el cliente invoca el boton global.
            return {
                "user": user_payload(client=client),
                "item": [],
                "response": ok,
            }
