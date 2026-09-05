"""Sistema persistente de amigos, separado del transporte y matchmaking."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from . import storage
from .clock import jst_now_string
from .identity import public_player_id
from .profiles import get_profile


_SOCIAL_LOCK = threading.RLock()
_STATE_FILE = "social.json"
_CHAT_LOG_FILE = "ChatLog.log"
_WORLD_FILE = "chat_world.json"
_PRIVATE_FILE = "chat_private.json"
_GUILD_FILE = "chat_guild.json"
_MAX_MESSAGES = 50


def _empty_state() -> dict:
    return {"version": 2, "players": {}, "friends": {}, "requests": {},
            "blacklists": {}}


def _append_message(messages: list[dict], message_entry: dict) -> None:
    messages.append(message_entry)
    if len(messages) > _MAX_MESSAGES:
        del messages[: len(messages) - _MAX_MESSAGES]


def _message_list(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)][-_MAX_MESSAGES:]


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_state() -> dict:
    target = storage.DATA / _STATE_FILE
    if not target.is_file():
        return _empty_state()
    state = storage.read_json_file(target, _empty_state())
    if not isinstance(state, dict):
        return _empty_state()
    for key in ("players", "friends", "requests", "blacklists"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    state["version"] = 2
    return state


def _load_message_stores(state: dict) -> dict:
    legacy = _message_list(state.get("messages", []))
    paths = {
        "world": storage.DATA / _WORLD_FILE,
        "private": storage.DATA / _PRIVATE_FILE,
        "guild": storage.DATA / _GUILD_FILE,
    }
    world_doc = storage.read_json_file(paths["world"], {}) if paths["world"].is_file() else {}
    private_doc = storage.read_json_file(paths["private"], {}) if paths["private"].is_file() else {}
    guild_doc = storage.read_json_file(paths["guild"], {}) if paths["guild"].is_file() else {}
    world = _message_list(world_doc.get("messages", []) if isinstance(world_doc, dict) else [])
    private = _message_list(private_doc.get("messages", []) if isinstance(private_doc, dict) else [])
    raw_guilds = guild_doc.get("guilds", {}) if isinstance(guild_doc, dict) else {}
    guilds = ({str(key): _message_list(value) for key, value in raw_guilds.items()
               if str(key).isdigit() and int(key) > 0}
              if isinstance(raw_guilds, dict) else {})
    if not paths["world"].is_file():
        world = _message_list([m for m in legacy
                               if _safe_int(m.get("message_type", 1), 1) == 1])
    if not paths["private"].is_file():
        private = _message_list([m for m in legacy
                                 if _safe_int(m.get("message_type", 1), 1) == 3])
    if not paths["guild"].is_file():
        for message in legacy:
            if _safe_int(message.get("message_type", 1), 1) != 2:
                continue
            try:
                guild_id = int(message.get("guild_id", 0))
            except (TypeError, ValueError):
                guild_id = 0
            if guild_id > 0:
                _append_message(guilds.setdefault(str(guild_id), []), message)
    return {"world": world, "private": private, "guilds": guilds}


def _save_state(state: dict) -> None:
    state.pop("messages", None)
    storage.atomic_write(
        storage.DATA / _STATE_FILE,
        json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        .encode("utf-8"),
    )


def _save_message_stores(stores: dict, state: dict) -> None:
    world = _message_list(stores.get("world", []))
    private = _message_list(stores.get("private", []))
    guilds = {str(key): _message_list(value)
              for key, value in stores.get("guilds", {}).items()
              if _message_list(value)}
    for filename, document in (
        (_WORLD_FILE, {"version": 1, "messages": world}),
        (_PRIVATE_FILE, {"version": 1, "messages": private}),
        (_GUILD_FILE, {"version": 1, "guilds": guilds}),
    ):
        storage.atomic_write(storage.DATA / filename, json.dumps(
            document, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"))
    lines = ["# Chat de Metal Slug Attack", "",
             "Vista diagnóstica de los almacenes separados de Chat.", ""]
    all_messages = world + private + [
        message for messages in guilds.values() for message in messages
    ]
    for message in sorted(
            all_messages, key=lambda item: _safe_int(item.get("timestamp", 0))):
        if not isinstance(message, dict):
            continue
        sender = str(message.get("sender", ""))
        player = state.get("players", {}).get(sender, {})
        name = str(player.get("name", sender)).replace("\n", " ").replace("\r", " ")
        text = str(message.get("message", "")).replace("\n", " ").replace("\r", " ")
        sent_at = str(message.get("datetime", ""))
        lines.append(f"- {sent_at} — {name} ({sender}): {text}")
    storage.atomic_write(
        storage.DATA / _CHAT_LOG_FILE,
        ("\n".join(lines) + "\n").encode("utf-8"),
    )


def _first(params: dict[str, list[str]], *names: str) -> str:
    for name in names:
        values = params.get(name)
        if values:
            return str(values[0]).strip()
    return ""


def _target_id(params: dict[str, list[str]]) -> str:
    return _first(
        params, "user_id", "user", "open_id", "friend_user_id",
        "friend_id", "target_user_id",
    )


def _unique(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value) for value in values if value))


class SocialController:
    """Implementa Friend usando un documento global atómico y versionado."""

    ROUTES = {
        "/friend/top", "/friend/search", "/friend/relation",
        "/friend/sever", "/mypage/get_message_info", "/mypage/chat",
        "/mypage/add_black_list", "/setting/remove_black_list",
    }

    def handles(self, path: str) -> bool:
        return path in self.ROUTES

    def record_guild_join(self, client: str, guild_id: int) -> None:
        """Publica un evento visible exclusivamente en el chat del Guild."""
        if guild_id <= 0:
            return
        with _SOCIAL_LOCK:
            state = _load_state()
            stores = _load_message_stores(state)
            player_id = self._register(state, client)
            player = state["players"].get(player_id, {})
            name = str(player.get("name", "PLAYER")).strip() or "PLAYER"
            now = datetime.now(timezone.utc)
            _append_message(stores["guilds"].setdefault(str(guild_id), []), {
                "id": f"guild-join-{int(now.timestamp() * 1000)}-{player_id[-6:]}",
                "sender": player_id,
                "message": f"{name} se ha unido al Guild.",
                "message_type": 2,
                "recipient": "",
                "guild_id": int(guild_id),
                "system_event": "guild_join",
                "timestamp": int(now.timestamp()),
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            })
            _save_state(state)
            _save_message_stores(stores, state)
            logging.info("Social guild_join guild=%s actor=%s", guild_id,
                         player_id)

    def client_for_player(self, player_id: str) -> str:
        """Resuelve una identidad pública a su instalación interna."""
        with _SOCIAL_LOCK:
            state = _load_state()
            player = state.get("players", {}).get(player_id, {})
            return str(player.get("client", "")) if isinstance(player, dict) else ""

    def record_friend_battle_invitation(
            self, client: str, recipient_id: str, room_id: str) -> bool:
        """Publica el mensaje de sistema que abre Friend Online 1on1."""
        if not recipient_id or not room_id:
            return False
        with _SOCIAL_LOCK:
            state = _load_state()
            stores = _load_message_stores(state)
            player_id = self._register(state, client)
            if recipient_id not in state["friends"].get(player_id, []):
                logging.warning(
                    "Friend battle rechazada actor=%s target=%s sin amistad",
                    player_id, recipient_id,
                )
                _save_state(state)
                return False
            if recipient_id not in state["players"]:
                return False
            invitation_param = json.dumps(
                {"type": 8, "id": room_id}, separators=(",", ":")
            )
            duplicate = any(
                isinstance(message, dict)
                and message.get("sender") == player_id
                and message.get("recipient") == recipient_id
                and message.get("param") == invitation_param
                for message in stores["private"]
            )
            if not duplicate:
                now = datetime.now(timezone.utc)
                _append_message(stores["private"], {
                    "id": f"friend-battle-{room_id}",
                    "sender": player_id,
                    "recipient": recipient_id,
                    "message": "FRIEND BATTLE",
                    "message_type": 3,
                    "param": invitation_param,
                    "system_event": "friend_online_1on1",
                    "timestamp": int(now.timestamp()),
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                })
            _save_state(state)
            _save_message_stores(stores, state)
            logging.info(
                "Friend battle invitacion actor=%s target=%s room=%s",
                player_id, recipient_id, room_id,
            )
            return True

    def record_guild_battle_invitation(
            self, client: str, guild_id: int, room_id: str,
            mode: str = "guild_online_2on2") -> bool:
        """Publica una sala Guild Online como mensaje nativo del gremio."""
        if guild_id <= 0 or not room_id:
            return False
        message_types = {
            # SystemMessageParam del cliente 7.13.0:
            #   3 -> GuildOnline1on1Param
            #   4 -> GuildOnline2on2Param
            # Los tipos 9 y 10 pertenecen respectivamente a United Friend
            # y Guild-to-WiS; ambos muestran texto, pero no crean el botón
            # que abre una sala Guild Online.
            "guild_online_1on1": 3,
            "guild_online_2on2": 4,
        }
        invitation_type = message_types.get(mode)
        if invitation_type is None:
            return False
        with _SOCIAL_LOCK:
            from .guild import guild_id_for_client
            if guild_id_for_client(client) != guild_id:
                return False
            state = _load_state()
            stores = _load_message_stores(state)
            player_id = self._register(state, client)
            invitation_param = json.dumps(
                {"type": invitation_type, "id": room_id},
                separators=(",", ":"),
            )
            messages = stores["guilds"].setdefault(str(guild_id), [])
            duplicate = any(
                isinstance(message, dict)
                and message.get("param") == invitation_param
                for message in messages
            )
            if not duplicate:
                now = datetime.now(timezone.utc)
                _append_message(messages, {
                    "id": f"guild-battle-{room_id}",
                    "sender": player_id,
                    "recipient": "",
                    "message": "GUILD 2ON2 BATTLE",
                    "message_type": 2,
                    "guild_id": guild_id,
                    "param": invitation_param,
                    "system_event": mode,
                    "timestamp": int(now.timestamp()),
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                })
            _save_state(state)
            _save_message_stores(stores, state)
            logging.info(
                "Guild battle invitacion guild=%s actor=%s room=%s mode=%s",
                guild_id, player_id, room_id, mode,
            )
            return True

    def menu_status(self, client: str) -> dict:
        """Devuelve los indicadores sociales que consume ``top_menu/top``.

        ``last_message_date`` no es una fecha decorativa: el cliente la
        compara con su ``message_last_hash`` local para decidir si debe
        mostrar una novedad en Mensaje. Debe cambiar solamente cuando el
        jugador tiene un mensaje entrante visible.
        """
        player_id = public_player_id(client)
        with _SOCIAL_LOCK:
            state = _load_state()
            stores = _load_message_stores(state)
            blocked = set(state["blacklists"].get(player_id, []))
            last_message_date = dict.fromkeys(
                ("world", "guild", "personal", "system"), "")
            from .guild import guild_id_for_client
            guild_id = guild_id_for_client(client)
            guild_messages = (
                stores["guilds"].get(str(guild_id), [])
                if guild_id > 0 else []
            )
            visible_messages = (
                stores["world"] + stores["private"] + guild_messages
            )
            for stored in sorted(
            visible_messages,
                    key=lambda item: (_safe_int(item.get("timestamp", 0)),
                                      str(item.get("id", ""))),
                    reverse=True):
                if not isinstance(stored, dict):
                    continue
                sender = str(stored.get("sender", ""))
                if not sender or sender == player_id or sender in blocked:
                    continue
                try:
                    message_type = int(stored.get("message_type", 1))
                except (TypeError, ValueError):
                    message_type = 1
                if message_type == 3:
                    if str(stored.get("recipient", "")) != player_id:
                        continue
                elif message_type == 2:
                    if _safe_int(stored.get("guild_id", 0)) != guild_id:
                        continue
                elif message_type != 1:
                    continue
                channel = {1: "world", 2: "guild", 3: "personal"}[message_type]
                # Native MessageLast stores opaque strings, not DateTime.
                # IDs distinguish messages delivered within the same second.
                candidate = str(stored.get("id", "")).strip()
                if candidate and not last_message_date[channel]:
                    last_message_date[channel] = candidate
            friend_count = len(_unique(state["friends"].get(player_id, [])))
        return {
            "last_message_date": last_message_date,
            "current_friend_num": friend_count,
        }

    def _register(self, state: dict, client: str) -> str:
        player_id = public_player_id(client)
        profile = get_profile(client, f"PLAYER {client}")
        state["players"][player_id] = {
            "client": client,
            "name": profile["name"][:64],
            "avatar": profile["avatar"],
            "language": profile["language"],
            "profile": profile["profile"][:256],
            "play_type": profile["play_type"],
            "is_accept": profile["is_accept"],
            "base_avatar": profile["base_avatar"],
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        state["friends"][player_id] = _unique(
            state["friends"].get(player_id, [])
        )
        state["requests"][player_id] = _unique(
            state["requests"].get(player_id, [])
        )
        state["blacklists"][player_id] = _unique(
            state["blacklists"].get(player_id, [])
        )
        return player_id

    @staticmethod
    def _refresh_player(state: dict, player_id: str) -> dict | None:
        player = state["players"].get(player_id)
        if not isinstance(player, dict):
            return None
        client = str(player.get("client", ""))
        if not client:
            return player
        profile = get_profile(client, str(player.get("name", "PLAYER")))
        player.update({
            "name": profile["name"][:64],
            "avatar": profile["avatar"],
            "language": profile["language"],
            "profile": profile["profile"][:256],
            "play_type": profile["play_type"],
            "is_accept": profile["is_accept"],
            "base_avatar": profile["base_avatar"],
        })
        return player

    @classmethod
    def _person(cls, state: dict, player_id: str,
                friend_type: int) -> dict | None:
        player = cls._refresh_player(state, player_id)
        if player is None:
            return None
        from .guild import guild_name_for_client
        client = str(player.get("client", ""))
        return {
            "user": player_id,
            "name": str(player.get("name", "PLAYER")),
            "level": 50,
            "avatar_id": int(player.get("avatar", 1)),
            "online_score": 0,
            # SceneMessage solo habilita CreatePersonalWindow para personas
            # de tipo 1. El tipo 0 se dibuja, pero no admite interacción.
            "type": 1,
            "detail": {
                "guild_name": guild_name_for_client(client) if client else "",
                "language": int(player.get("language", 8)),
                "profile": str(player.get("profile", "")),
                "friend_id": 0,
                "play_type": int(player.get("play_type", 0)),
                # El cliente habilita Enviar/Aceptar cuando ON llega como 1.
                "is_accept": int(player.get("is_accept", 0)),
                "friend_type": friend_type,
                "last_login_time": "2023-01-12 15:30:00",
                "arena_honor": 0,
            },
        }

    def _entry(self, state: dict, player_id: str,
               friend_type: int) -> dict | None:
        person = self._person(state, player_id, friend_type)
        return {"person": person, "remain_time": 0} if person else None

    @staticmethod
    def _friend_type(state: dict, viewer: str, target: str) -> int:
        if target in state["friends"].get(viewer, []):
            return 1
        if viewer in state["requests"].get(target, []):
            return 2  # solicitud enviada por viewer
        if target in state["requests"].get(viewer, []):
            return 3  # solicitud recibida por viewer
        return 0

    def _top(self, state: dict, player_id: str, ok: dict) -> dict:
        friends = [
            self._entry(state, target, 1)
            for target in _unique(state["friends"].get(player_id, []))
        ]
        pending = [
            self._entry(state, source, 3)
            for source in _unique(state["requests"].get(player_id, []))
        ]
        return {
            "friend": [entry for entry in friends if entry],
            "pending": [entry for entry in pending if entry],
            "current_friend_num": len([entry for entry in friends if entry]),
            "response": ok,
        }

    def _search(self, state: dict, player_id: str,
                params: dict[str, list[str]], ok: dict) -> dict:
        query = _first(params, "name", "keyword").casefold()
        results = []
        friend_ids = set(state["friends"].get(player_id, []))
        pending_ids = set(state["requests"].get(player_id, []))
        for target_id, player in state["players"].items():
            if target_id == player_id or not isinstance(player, dict):
                continue
            if query and query not in str(player.get("name", "")).casefold():
                continue
            relation = self._friend_type(state, player_id, target_id)
            person = self._person(state, target_id, relation)
            if person:
                results.append({"person": person, "remain_time": 0})
            if len(results) >= 50:
                break
        # Algunas versiones leen `friend`; otras conservan el nombre `search`.
        # Se entregan ambas vistas del mismo resultado sin duplicar estado.
        return {"friend": results, "search": results,
                "pending": list(pending_ids), "response": ok}

    def _relation(self, state: dict, stores: dict, player_id: str,
                  params: dict[str, list[str]]) -> None:
        target = _target_id(params)
        if target == player_id or target not in state["players"]:
            return
        action = _first(params, "action", "relation").lower()
        explicit_accept = _first(params, "is_accept", "accept")
        incoming = target in state["requests"].get(player_id, [])

        if action in {"reject", "deny", "decline"} or (
                incoming and explicit_accept == "0"):
            state["requests"][player_id] = [
                value for value in state["requests"].get(player_id, [])
                if value != target
            ]
            return

        if incoming and (explicit_accept == "1" or action == "accept"):
            state["requests"][player_id] = [
                value for value in state["requests"].get(player_id, [])
                if value != target
            ]
            for owner, friend in ((player_id, target), (target, player_id)):
                values = _unique(state["friends"].get(owner, []))
                if friend not in values:
                    values.append(friend)
                state["friends"][owner] = values
            return

        # Peticiones cruzadas se aceptan automáticamente para evitar dos
        # estados pendientes contradictorios.
        if target in state["requests"].get(player_id, []):
            state["requests"][player_id] = [
                value for value in state["requests"][player_id]
                if value != target
            ]
            for owner, friend in ((player_id, target), (target, player_id)):
                values = _unique(state["friends"].get(owner, []))
                if friend not in values:
                    values.append(friend)
                state["friends"][owner] = values
            return

        target_player = self._refresh_player(state, target) or {}
        if int(target_player.get("is_accept", 0)) != 1:
            logging.info(
                "Social relation rechazada: target=%s no acepta solicitudes",
                target,
            )
            return

        if target not in state["friends"].get(player_id, []):
            requests = _unique(state["requests"].get(target, []))
            if player_id not in requests:
                requests.append(player_id)
                now = datetime.now(timezone.utc)
                _append_message(stores["private"], {
                    "id": f"friend-{int(now.timestamp() * 1000)}-{player_id[-6:]}",
                    "sender": player_id,
                    "recipient": target,
                    "message": "FRIEND REQUEST",
                    "message_type": 3,
                    "timestamp": int(now.timestamp()),
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                })
            state["requests"][target] = requests

    @staticmethod
    def _sever(state: dict, player_id: str,
               params: dict[str, list[str]]) -> None:
        target = _target_id(params)
        if not target:
            return
        for owner, other in ((player_id, target), (target, player_id)):
            state["friends"][owner] = [
                value for value in state["friends"].get(owner, [])
                if value != other
            ]
            state["requests"][owner] = [
                value for value in state["requests"].get(owner, [])
                if value != other
            ]

    def _message_info(self, state: dict, stores: dict, player_id: str,
                      params: dict[str, list[str]], ok: dict) -> dict:
        try:
            message_type = int(_first(params, "message_type") or 1)
        except ValueError:
            message_type = 1
        languages = {
            str(value) for value in params.get("language_filter[]", [])
        } | {str(value) for value in params.get("language_filter", [])}
        blocked = set(state["blacklists"].get(player_id, []))
        guild_id = 0
        if message_type == 2:
            player = state["players"].get(player_id, {})
            client = str(player.get("client", "")) if isinstance(player, dict) else ""
            if client:
                from .guild import guild_id_for_client
                guild_id = guild_id_for_client(client)
        if message_type == 1:
            stored_messages = stores["world"]
        elif message_type == 3:
            stored_messages = stores["private"]
        elif message_type == 2 and guild_id > 0:
            stored_messages = stores["guilds"].get(str(guild_id), [])
        else:
            stored_messages = []
        world = []
        # La interfaz del cliente no invierte el arreglo: espera recibir el
        # mensaje más reciente primero.
        for stored in reversed(stored_messages[-_MAX_MESSAGES:]):
            if not isinstance(stored, dict) or stored.get("sender") in blocked:
                continue
            if _safe_int(stored.get("message_type", 1), 1) != message_type:
                continue
            if message_type == 2:
                try:
                    stored_guild = int(stored.get("guild_id", 0))
                except (TypeError, ValueError):
                    stored_guild = 0
                if guild_id <= 0 or stored_guild != guild_id:
                    continue
            sender = str(stored.get("sender", ""))
            recipient = str(stored.get("recipient", ""))
            if message_type == 3:
                # Los whispers pertenecen únicamente a sus dos participantes.
                if not recipient or player_id not in {sender, recipient}:
                    continue
                conversation_with = _first(params, "open_id")
                if (conversation_with
                        and conversation_with not in {sender, recipient}):
                    continue
            player = state["players"].get(sender, {})
            language = str(player.get("language", 8))
            if languages and "0" not in languages and language not in languages:
                continue
            friend_type = self._friend_type(state, player_id, sender)
            person = self._person(state, sender, friend_type)
            if not person:
                continue
            # El autor de un mensaje ajeno es navegable. El perfil propio se
            # muestra igual, pero SceneMessage no debe habilitar el toque.
            person["type"] = 0 if sender == player_id else 1
            try:
                sent_timestamp = int(stored.get("timestamp", 0))
            except (TypeError, ValueError):
                sent_timestamp = 0
            if sent_timestamp <= 0:
                try:
                    parsed_date = datetime.strptime(
                        str(stored.get("datetime", "")), "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                    sent_timestamp = int(parsed_date.timestamp())
                except ValueError:
                    logging.warning("Mensaje omitido: fecha persistida invalida id=%s",
                                    stored.get("id", ""))
                    continue
            entry = {
                # El id de salida incluye la relación vigente para impedir
                # que SceneMessage reutilice un PersonData obsoleto después
                # de aceptar, rechazar o terminar una amistad. Esto también
                # es necesario en el chat mundial, desde donde se abren la
                # mayoría de perfiles.
                "id": (
                    f"{str(stored.get('id', ''))}-r"
                    f"{person['detail']['friend_type']}"
                    f"a{person['detail']['is_accept']}"
                ),
                "user": sender,
                "person": person,
                "name": person["name"],
                "avatar_id": person["avatar_id"],
                "message": str(stored.get("message", "")),
                "text": str(stored.get("message", "")),
                "param": str(stored.get("param", "")),
                # parseMessageInfo constructs DateTime(const char*), not an
                # epoch parser. Native wire dates use the server's JST clock.
                "timestamp": jst_now_string(
                    datetime.fromtimestamp(sent_timestamp, timezone.utc)),
                "datetime": sent_timestamp,
                "date_time": str(stored.get("datetime", "")),
                "send_date_time": str(stored.get("datetime", "")),
                "message_type": int(stored.get("message_type", 1)),
            }
            if message_type == 3:
                to_person = self._person(
                    state, recipient,
                    self._friend_type(state, player_id, recipient),
                )
                if not to_person:
                    continue
                to_person["type"] = 0 if recipient == player_id else 1
                entry["to_person"] = to_person
                # SceneMessage conserva las filas privadas por id. Si dos
                # usuarios dejan de ser amigos, reutilizar el mismo id puede
                # dejar en caché el PersonData con friend_type=1 aunque el
                # servidor ya entregue la relación 0. La revisión forma parte
                # únicamente del contrato de salida; el id persistente del
                # mensaje no se modifica.
                entry["id"] += (
                    f"{to_person['detail']['friend_type']}"
                    f"a{to_person['detail']['is_accept']}"
                )
            elif message_type == 2:
                entry["guild_id"] = guild_id
            world.append(entry)
        # `chat_chance` es el saldo disponible. El cliente admite como máximo
        # 10, por lo que reponerlo a 10 en cada consulta y después de cada
        # envío permite escribir indefinidamente sin parchear el APK.
        logging.info("Social message_info actor=%s type=%d mensajes=%d",
                     player_id, message_type, len(world))
        return {"message_info": world, "chat_chance": 10,
                "response": ok}

    def _chat(self, state: dict, stores: dict, player_id: str,
              params: dict[str, list[str]], ok: dict) -> dict:
        message = _first(params, "message")[:256].strip()
        if message:
            now = datetime.now(timezone.utc)
            try:
                message_type = int(_first(params, "message_type") or 1)
            except ValueError:
                message_type = 1
            recipient = _first(params, "open_id") if message_type == 3 else ""
            if message_type == 3 and (
                    not recipient or recipient == player_id
                    or recipient not in state["players"]):
                logging.warning(
                    "Social whisper ignorado actor=%s target=%s",
                    player_id, recipient,
                )
                return self._message_info(state, stores, player_id, params, ok)
            guild_id = 0
            if message_type == 2:
                player = state["players"].get(player_id, {})
                client = str(player.get("client", "")) if isinstance(player, dict) else ""
                if client:
                    from .guild import guild_id_for_client
                    guild_id = guild_id_for_client(client)
                if guild_id <= 0:
                    logging.warning(
                        "Social guild chat rechazado actor=%s sin membresia",
                        player_id,
                    )
                    return self._message_info(state, stores, player_id, params, ok)
            if message_type == 1:
                destination = stores["world"]
            elif message_type == 3:
                destination = stores["private"]
            elif message_type == 2:
                destination = stores["guilds"].setdefault(str(guild_id), [])
            else:
                logging.warning("Social chat tipo no soportado=%s", message_type)
                return self._message_info(state, stores, player_id, params, ok)
            _append_message(destination, {
                "id": f"{int(now.timestamp() * 1000)}-{player_id[-6:]}",
                "sender": player_id,
                "message": message,
                "message_type": message_type,
                "recipient": recipient,
                "guild_id": guild_id,
                "timestamp": int(now.timestamp()),
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            })
            logging.info("Social chat actor=%s chars=%d", player_id,
                         len(message))
        return self._message_info(state, stores, player_id, params, ok)

    def _add_blacklist(self, state: dict, player_id: str,
                       params: dict[str, list[str]]) -> None:
        target = _target_id(params)
        if not target or target == player_id or target not in state["players"]:
            return
        values = _unique(state["blacklists"].get(player_id, []))
        if target not in values:
            values.append(target)
        state["blacklists"][player_id] = values
        self._sever(state, player_id, {"user_id": [target]})

    @staticmethod
    def _remove_blacklist(state: dict, player_id: str,
                          params: dict[str, list[str]]) -> None:
        target = _target_id(params)
        state["blacklists"][player_id] = [
            value for value in state["blacklists"].get(player_id, [])
            if value != target
        ]
    def handle(self, path: str, client: str,
               params: dict[str, list[str]], ok: dict) -> dict | None:
        if not self.handles(path):
            return None
        with _SOCIAL_LOCK:
            state = _load_state()
            stores = _load_message_stores(state)
            player_id = self._register(state, client)
            if path == "/mypage/get_message_info":
                payload = self._message_info(state, stores, player_id, params, ok)
            elif path == "/mypage/chat":
                payload = self._chat(state, stores, player_id, params, ok)
            elif path == "/mypage/add_black_list":
                self._add_blacklist(state, player_id, params)
                payload = self._message_info(state, stores, player_id, {}, ok)
            elif path == "/setting/remove_black_list":
                self._remove_blacklist(state, player_id, params)
                payload = {"black_list": self.blacklist_entries(
                    state, player_id), "response": ok}
            elif path == "/friend/search":
                payload = self._search(state, player_id, params, ok)
            else:
                if path == "/friend/relation":
                    self._relation(state, stores, player_id, params)
                    logging.info("Social relation actor=%s target=%s",
                                 player_id, _target_id(params))
                elif path == "/friend/sever":
                    self._sever(state, player_id, params)
                    logging.info("Social sever actor=%s target=%s",
                                 player_id, _target_id(params))
                payload = self._top(state, player_id, ok)
            _save_state(state)
            _save_message_stores(stores, state)
            if "message_info" in payload:
                payload["last_message_date"] = self.menu_status(client)["last_message_date"]
            return payload

    def blacklist_entries(self, state: dict, player_id: str) -> list[dict]:
        return [
            person for person in (
                self._person(state, target, 0)
                for target in _unique(state["blacklists"].get(player_id, []))
            ) if person
        ]

    def blacklist(self, client: str) -> list[dict]:
        with _SOCIAL_LOCK:
            state = _load_state()
            player_id = self._register(state, client)
            result = self.blacklist_entries(state, player_id)
            _save_state(state)
            return result
