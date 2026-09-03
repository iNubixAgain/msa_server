"""Guild persistente: membresia, solicitudes, permisos y administracion."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from . import storage
from .identity import public_player_id
from .profiles import get_profile
from .content import rental_soldier


_LOCK = threading.RLock()
_STATE_FILE = "guilds.json"
_MAX_MEMBERS = 50


def _empty_state() -> dict:
    return {"version": 1, "next_id": 1, "guilds": {}, "memberships": {}}


def _load_state() -> dict:
    target = storage.DATA / _STATE_FILE
    state = storage.read_json_file(target, _empty_state()) if target.is_file() else _empty_state()
    if not isinstance(state, dict):
        state = _empty_state()
    state.setdefault("version", 1)
    state.setdefault("next_id", 1)
    state.setdefault("guilds", {})
    state.setdefault("memberships", {})
    if not isinstance(state["guilds"], dict):
        state["guilds"] = {}
    if not isinstance(state["memberships"], dict):
        state["memberships"] = {}
    _repair_state(state)
    return state


def _repair_state(state: dict) -> None:
    """Reconstruye referencias para que miembros e índices nunca discrepen."""
    repaired_guilds: dict[str, dict] = {}
    memberships: dict[str, int] = {}
    max_id = 0
    candidates = []
    for key, guild in state["guilds"].items():
        if not isinstance(guild, dict):
            continue
        try:
            guild_id = int(guild.get("guild_id", key))
        except (TypeError, ValueError):
            continue
        if guild_id <= 0:
            continue
        candidates.append((guild_id, guild))
    for guild_id, guild in sorted(candidates, key=lambda item: item[0]):
        members = []
        local_ids = set()
        for entry in guild.get("members", []):
            if not isinstance(entry, dict):
                continue
            player_id = str(entry.get("player_id", "")).strip()
            client = str(entry.get("client", "")).strip()
            if (not player_id or not client or player_id in local_ids or
                    player_id in memberships):
                continue
            local_ids.add(player_id)
            memberships[player_id] = guild_id
            members.append(entry)
        if not members:
            continue
        leader = str(guild.get("leader", ""))
        if leader not in local_ids:
            leader = str(members[0]["player_id"])
        requests = []
        request_ids = set()
        for entry in guild.get("requests", []):
            if not isinstance(entry, dict):
                continue
            player_id = str(entry.get("player_id", "")).strip()
            client = str(entry.get("client", "")).strip()
            if (not player_id or not client or player_id in local_ids or
                    player_id in memberships or player_id in request_ids):
                continue
            request_ids.add(player_id)
            requests.append(entry)
        guild["guild_id"] = guild_id
        guild["leader"] = leader
        guild["members"] = members
        guild["requests"] = requests
        repaired_guilds[str(guild_id)] = guild
        max_id = max(max_id, guild_id)
    for guild in repaired_guilds.values():
        guild["requests"] = [
            entry for entry in guild.get("requests", [])
            if str(entry.get("player_id", "")) not in memberships
        ]
    state["guilds"] = repaired_guilds
    state["memberships"] = memberships
    try:
        next_id = int(state.get("next_id", 1))
    except (TypeError, ValueError):
        next_id = 1
    state["next_id"] = max(next_id, max_id + 1, 1)


def _save_state(state: dict) -> None:
    storage.atomic_write(
        storage.DATA / _STATE_FILE,
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


def _first(params: dict[str, list[str]], *names: str, default: str = "") -> str:
    for name in names:
        values = params.get(name)
        if values:
            return str(values[0]).strip()
    return default


def _integer(params: dict[str, list[str]], *names: str, default: int = 0) -> int:
    try:
        return int(_first(params, *names, default=str(default)))
    except (TypeError, ValueError):
        return default


def guild_id_for_client(client: str) -> int:
    with _LOCK:
        state = _load_state()
        value = state["memberships"].get(public_player_id(client), 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0


def guild_kind_for_client(client: str) -> int:
    """GuildKind nativo, distinto del ID persistente del Guild."""
    return 1 if guild_id_for_client(client) > 0 else 0


def guild_name_for_client(client: str) -> str:
    with _LOCK:
        state = _load_state()
        guild_id = state["memberships"].get(public_player_id(client))
        guild = state["guilds"].get(str(guild_id), {})
        return str(guild.get("name", "")) if isinstance(guild, dict) else ""


def guild_event_context(client: str) -> dict | None:
    """Vista inmutable mínima para controladores de eventos de Guild."""
    with _LOCK:
        state = _load_state()
        guild_id = state["memberships"].get(public_player_id(client))
        guild = state["guilds"].get(str(guild_id))
        if not isinstance(guild, dict):
            return None
        view = GuildController()._guild_payload(guild)
        return {
            "guild_id": int(view["guild_id"]), "name": view["name"],
            "logo_id": int(view["logo_id"]),
            "language": int(view["language"]),
            "role": 1 if str(guild.get("leader")) == public_player_id(client) else 3,
            "members": list(view["player"]),
        }


class GuildController:
    ROUTES = {
        "/guild/search_guild", "/guild/build_guild",
        "/guild/request_enrollment", "/guild/delete_request",
        "/guild/join_guild", "/guild/join_approval",
        "/guild/command_member", "/guild/break_guild",
        "/guild/edit_guild_info", "/guild/gj",
        "/guild/get_guild_bonus", "/guild/camp_soldier",
        "/guild/return_soldier", "/guild/join_onetime_guild",
        "/setting/withdrawal_guild",
        "/guild_battle/top",
    }

    def handles(self, path: str) -> bool:
        return path in self.ROUTES

    @staticmethod
    def _error(ok: dict, code: int) -> dict:
        response = dict(ok)
        response["error_code"] = code
        return {"result": 0, "status": 0, "response": response}

    @staticmethod
    def _player(player_id: str, client: str, role: int = 0) -> dict:
        profile = get_profile(client, "PLAYER")
        return {
            "user": player_id, "open_id": player_id,
            "name": profile["name"], "level": 50,
            "avatar_id": profile["avatar"], "language": profile["language"],
            "role": role, "position": role, "authority": role,
            "is_master": int(role == 1), "is_owner": int(role == 1),
            "last_login_time": "2023-01-12 15:30:00",
            "online_score": 0, "gj": 0,
        }

    def _guild_payload(self, guild: dict) -> dict:
        leader = str(guild.get("leader", ""))
        members = []
        for entry in guild.get("members", []):
            if not isinstance(entry, dict):
                continue
            player_id = str(entry.get("player_id", ""))
            client = str(entry.get("client", ""))
            if player_id and client:
                members.append(self._player(player_id, client, 1 if player_id == leader else 0))
        requests = []
        for entry in guild.get("requests", []):
            if isinstance(entry, dict) and entry.get("player_id") and entry.get("client"):
                requests.append(self._player(str(entry["player_id"]), str(entry["client"]), 0))
        guild_id = int(guild["guild_id"])
        return {
            "guild_id": guild_id, "id": guild_id,
            "name": str(guild.get("name", "GUILD")),
            "guild_name": str(guild.get("name", "GUILD")),
            "logo": int(guild.get("logo", 1)),
            "logo_id": int(guild.get("logo", 1)),
            "profile": str(guild.get("profile", "")),
            "comment": str(guild.get("profile", "")),
            "language": int(guild.get("language", 8)),
            "play_type": int(guild.get("play_type", 0)),
            "join_type": int(guild.get("join_type", 0)),
            "approval": int(guild.get("join_type", 0)),
            "open": int(guild.get("open", 1)),
            "limit_level": int(guild.get("limit_level", 1)),
            "master_id": leader, "leader": leader,
            "member_num": len(members), "max_member_num": _MAX_MEMBERS,
            "member": members, "members": members,
            "player": members,
            "request": requests, "requests": requests,
            "applicant": requests,
            "guild_level": 1, "level": 1, "guild_point": 0,
            "ranking": 0, "activity_gauge": 0,
            "gj": int(guild.get("gj", 0)),
            "camp_soldier": list(guild.get("camp_soldier", [])),
        }

    def _native_guild_state(self, guild: dict) -> dict:
        """Contrato exacto que parseGuild exige en GuildBattle_Top."""
        view = self._guild_payload(guild)
        native_members = []
        for member in view["player"]:
            # parseGuild procesa cada elemento como Person directamente y
            # después lee ``role`` y ``online_score`` del mismo objeto. No
            # existe un contenedor ``person`` en este contrato.
            native_member = {
                "user": member["user"], "name": member["name"],
                "level": member["level"], "avatar_id": member["avatar_id"],
                "online_score": member["online_score"], "type": 1,
                # Matriz nativa de GuildManage: 1 habilita las acciones del
                # owner y 3 representa al miembro normal, para quien solo se
                # habilita "abandonar Guild". El 0 significa sin afiliación.
                "role": 1 if member["is_owner"] else 3,
                "detail": {
                    "guild_name": view["name"],
                    "language": member["language"], "profile": "",
                    "friend_id": 0, "play_type": 0, "is_accept": 1,
                    "friend_type": 0,
                    "last_login_time": member["last_login_time"],
                    "arena_honor": 0,
                },
            }
            native_members.append(native_member)
        native_applicants = []
        for member in view["applicant"]:
            native_applicants.append({
                "user": member["user"], "name": member["name"],
                "level": member["level"],
                "avatar_id": member["avatar_id"],
                "online_score": 0, "type": 1, "role": 0,
                "detail": {
                    "guild_name": "", "language": member["language"],
                    "profile": "", "friend_id": 0, "play_type": 0,
                    "is_accept": 1, "friend_type": 0,
                    "last_login_time": member["last_login_time"],
                    "arena_honor": 0,
                },
            })
        guild_info = {
            "guild_id": view["guild_id"], "name": view["name"],
            "guild_name": view["name"], "language": view["language"],
            "logo_id": view["logo_id"], "member_num": view["member_num"],
            "activity_gauge": view["activity_gauge"],
            "open": bool(view["open"]),
            "limit_level": view["limit_level"], "comment": view["comment"],
        }
        return {
            "gp": 0, "sp": 0, "gj": view["gj"],
            "request": bool(native_applicants), "bonus": 0,
            "guild_info": guild_info,
            # ``camp_soldier`` es persistencia interna (user + unit_id), no
            # el UnitParam completo que parseGuild exige en ``my_soldier``.
            # Publicar esas referencias parciales cierra SceneGuild al
            # renderizar un gremio que tenga al menos un soldado acampado.
            "my_soldier": [],
            "member": native_members,
            "applicant": native_applicants,
            "gj_info": [],
            # applicant_guild_info se omite: su mera existencia representa
            # el Guild temporal de Guild Ops, incluso si el objeto está vacío.
        }

    @staticmethod
    def _empty_native_guild_state() -> dict:
        """Estado que limpia la transacción Guild residente del cliente."""
        # Omitir guild_info no borra el accessor anterior. Debemos parsear una
        # ficha completa y válida con ID 0 para sobrescribir nombre, logo y
        # afiliación que el cliente conserva entre aperturas de SceneGuild.
        return {
            "gp": 0, "sp": 0, "gj": 0,
            "request": False, "bonus": 0,
            "guild_info": {
                "guild_id": 0, "name": "", "guild_name": "",
                "language": 0, "logo_id": 0, "member_num": 0,
                "activity_gauge": 0, "open": False,
                "limit_level": 0, "comment": "",
            },
            "my_soldier": [], "member": [], "applicant": [],
            "gj_info": [],
        }

    @staticmethod
    def _empty_raid() -> dict:
        return {
            "area": [], "current_stage_id": 0,
            "early_end_time": "2023-01-12 15:30:00",
            "play": 0, "enemy_data": {}, "ranking": [], "point": 0,
            "booty": [], "stock": [], "tenderer": [], "bid": 0,
        }

    @staticmethod
    def _snatch_state(guild: dict | None) -> dict:
        """Estado persistente y conservador de Snatch Wars Season 11."""
        if guild is None:
            return {
                "event_id": 81, "season": 11, "point": 0,
                "current_stage_id": 0, "play": 0,
                "area": [], "stock": [], "tenderer": [], "bid": 0,
                "booty": [], "ranking": [],
            }
        state = guild.setdefault("snatch_wars", {})
        state.setdefault("event_id", 81)
        state.setdefault("season", 11)
        state.setdefault("point", 0)
        state.setdefault("current_stage_id", 0)
        state.setdefault("play", 1)
        state.setdefault("stock", [])
        state.setdefault("tenderer", [])
        state.setdefault("bid", 0)
        state.setdefault("booty", [])
        state.setdefault("ranking", [])
        state.setdefault("area", [])
        return state

    def _snatch_raid(self, guild: dict | None) -> dict:
        state = self._snatch_state(guild)
        return {
            "event_id": state["event_id"], "season": state["season"],
            "area": list(state["area"]),
            "current_stage_id": int(state.get("current_stage_id", 0)),
            "early_end_time": "2037-12-31 23:59:59",
            "play": int(state.get("play", 0)), "enemy_data": {},
            "ranking": list(state["ranking"]),
            "point": int(state["point"]), "booty": list(state["booty"]),
            "stock": list(state["stock"]),
            "tenderer": list(state["tenderer"]),
            "bid": int(state["bid"]),
        }

    @staticmethod
    def _entry(player_id: str, client: str) -> dict:
        return {"player_id": player_id, "client": client,
                "joined_at": datetime.now(timezone.utc).isoformat()}

    @staticmethod
    def _find_guild(state: dict, params: dict[str, list[str]], actor: str) -> dict | None:
        guild_id = _first(params, "guild_id", "id")
        if not guild_id:
            guild_id = str(state["memberships"].get(actor, ""))
        return state["guilds"].get(str(guild_id))

    def _base(self, client: str, guild: dict | None, ok: dict) -> dict:
        from .users import user_payload
        payload = {"user": user_payload(client=client), "response": ok}
        if guild is not None:
            view = self._guild_payload(guild)
            payload.update({
                # Todos los extractores Guild_* interpretan la raíz guild
                # mediante parseGuild, no como una ficha GuildInfo plana.
                "guild": self._native_guild_state(guild),
                "guild_info": view,
                "my_guild_info": view,
            })
        else:
            # La respuesta conserva el objeto que limpia el accessor Guild
            # residente. No publicar aliases vacíos: su mera presencia activa
            # extractores GuildInfo que esperan todos los campos obligatorios.
            payload["guild"] = self._empty_native_guild_state()
        return payload

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               ok: dict) -> dict | None:
        if path not in self.ROUTES:
            return None
        actor = public_player_id(client)
        with _LOCK:
            state = _load_state()
            guild = self._find_guild(state, params, actor)

            if path == "/guild_battle/top":
                from .users import user_payload
                message_last = {
                    "world": "2023-01-12 15:30:00",
                    "guild": "2023-01-12 15:30:00",
                    "personal": "2023-01-12 15:30:00",
                    "system": "2023-01-12 15:30:00",
                }
                payload = {
                    "user": user_payload(client=client),
                    "guild_build_price": False,
                    "join_limit_time": "2023-01-12 15:30:00",
                    "guild": (self._native_guild_state(guild)
                              if guild is not None
                              else self._empty_native_guild_state()),
                    "raid": self._empty_raid(),
                    "rental_soldier": rental_soldier(),
                    "last_message_date": message_last,
                    "response": ok,
                }
                # GuildBattle_Top extrae toda la afiliación desde ``guild``.
                # Duplicar además la vista extendida en ``guild_info`` y
                # ``my_guild_info`` repetía cada miembro seis veces más. Al
                # crecer un Guild, SceneGuild terminaba construyendo decenas
                # de PersonData redundantes y podía cerrarse durante el parseo.
                # Las rutas de creación/administración conservan esas vistas
                # porque sus extractores sí las utilizan directamente.
                return payload

            if path.startswith("/guild_battle/"):
                if guild is None:
                    return self._error(ok, 404)
                if actor not in state["memberships"]:
                    return self._error(ok, 403)
                snatch = self._snatch_state(guild)
                if path == "/guild_battle/playable":
                    snatch["play"] = 1
                elif path == "/guild_battle/area_keep":
                    snatch["current_stage_id"] = _integer(
                        params, "stage_id", "area_id", "id"
                    )
                elif path == "/guild_battle/raid_start":
                    snatch["play"] = 1
                    snatch["current_stage_id"] = _integer(
                        params, "stage_id", "area_id", "id",
                        default=int(snatch.get("current_stage_id", 0)),
                    )
                elif path == "/guild_battle/raid_end":
                    gained = max(0, _integer(
                        params, "point", "score", "damage", default=0
                    ))
                    snatch["point"] = int(snatch.get("point", 0)) + gained
                elif path == "/guild_battle/bid":
                    snatch["bid"] = max(0, _integer(
                        params, "bid", "point", "num", default=0
                    ))
                elif path == "/guild_battle/stock":
                    snatch["stock"] = []
                _save_state(state)
                payload = self._base(client, guild, ok)
                payload.update({
                    "raid": self._snatch_raid(guild),
                    "continue_medal": 0,
                    "relief_supplies_contents": {"contents": []},
                    "item": [], "reward": [], "result": 1,
                })
                return payload

            if path == "/guild/search_guild":
                query = _first(params, "name", "keyword").casefold()
                results = []
                for candidate in state["guilds"].values():
                    if not isinstance(candidate, dict):
                        continue
                    if query and query not in str(candidate.get("name", "")).casefold():
                        continue
                    results.append(self._guild_payload(candidate))
                    if len(results) >= 50:
                        break
                from .users import user_payload
                return {
                    "user": user_payload(client=client),
                    "guild": results, "guild_list": results,
                    "num": len(results),
                    "search": results, "applicant": results,
                    "my_guild_info": (
                        self._guild_payload(guild) if guild is not None else {}
                    ),
                    "response": ok,
                }

            if path == "/guild/build_guild":
                if actor in state["memberships"]:
                    return self._error(ok, 409)
                name = _first(params, "name", "guild_name", default="NEW GUILD")[:32]
                if not name:
                    return self._error(ok, 400)
                guild_id = int(state["next_id"])
                state["next_id"] = guild_id + 1
                guild = {
                    "guild_id": guild_id, "name": name,
                    "logo": _integer(params, "logo", "logo_id", default=1),
                    "profile": _first(params, "profile", "comment", "description")[:256],
                    "language": _integer(params, "language", default=8),
                    "play_type": _integer(params, "play_type"),
                    "join_type": _integer(params, "join_type", "approval", "is_approval"),
                    "open": _integer(params, "open", default=1),
                    "limit_level": _integer(params, "level", "limit_level", default=1),
                    "leader": actor, "members": [self._entry(actor, client)],
                    "requests": [], "camp_soldier": [], "gj": 0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                state["guilds"][str(guild_id)] = guild
                state["memberships"][actor] = guild_id
                _save_state(state)
                logging.info("GUILD creado id=%s lider=%s nombre=%r", guild_id, actor, name)
                return self._base(client, guild, ok)

            if guild is None:
                return self._error(ok, 404)
            guild_id = int(guild["guild_id"])
            leader = str(guild.get("leader", ""))
            members = guild.setdefault("members", [])
            requests = guild.setdefault("requests", [])

            if path in {"/guild/request_enrollment", "/guild/join_guild",
                        "/guild/join_onetime_guild"}:
                if path == "/guild/join_onetime_guild":
                    # Guild Ops todavía no crea Guilds temporales. No mezclar
                    # esta modalidad con la membresía ordinaria persistente.
                    return self._error(ok, 501)
                if actor in state["memberships"]:
                    return self._error(ok, 409)
                if len(members) >= _MAX_MEMBERS:
                    return self._error(ok, 413)
                requires_approval = int(guild.get("join_type", 0)) != 0
                if path == "/guild/request_enrollment" or requires_approval:
                    # El cliente modela una sola solicitud pendiente.
                    for candidate in state["guilds"].values():
                        if candidate is guild:
                            continue
                        candidate["requests"] = [
                            item for item in candidate.get("requests", [])
                            if str(item.get("player_id")) != actor
                        ]
                    if not any(str(item.get("player_id")) == actor for item in requests):
                        requests.append(self._entry(actor, client))
                else:
                    for candidate in state["guilds"].values():
                        candidate["requests"] = [
                            item for item in candidate.get("requests", [])
                            if str(item.get("player_id")) != actor
                        ]
                    members.append(self._entry(actor, client))
                    state["memberships"][actor] = guild_id
                _save_state(state)
                payload = self._base(client, guild, ok)
                if actor in state["memberships"]:
                    payload["_server_events"] = [{
                        "type": "guild_join", "client": client,
                        "guild_id": guild_id,
                    }]
                return payload

            if path == "/guild/delete_request":
                guild["requests"] = [item for item in requests
                                     if str(item.get("player_id")) != actor]
                _save_state(state)
                return self._base(client, guild, ok)

            if path == "/guild/join_approval":
                if actor != leader:
                    return self._error(ok, 403)
                target = _first(params, "member_id", "user", "open_id", "user_id")
                approve = _integer(params, "approval", "is_approval", "command", "status", default=1) != 0
                request = next((item for item in requests if str(item.get("player_id")) == target), None)
                if request is None:
                    return self._error(ok, 404)
                guild["requests"] = [item for item in requests if item is not request]
                joined_now = False
                if approve and target not in state["memberships"] and len(members) < _MAX_MEMBERS:
                    members.append(request)
                    state["memberships"][target] = guild_id
                    joined_now = True
                _save_state(state)
                payload = self._base(client, guild, ok)
                if joined_now:
                    payload["_server_events"] = [{
                        "type": "guild_join",
                        "client": str(request.get("client", "")),
                        "guild_id": guild_id,
                    }]
                return payload

            if path == "/guild/command_member":
                if actor != leader:
                    return self._error(ok, 403)
                target = _first(params, "member_id", "user", "open_id", "user_id")
                if not target or target == leader:
                    return self._error(ok, 400)
                if not any(str(item.get("player_id")) == target for item in members):
                    return self._error(ok, 404)
                command = _integer(params, "command", "type", "status", default=0)
                if command in {1, 2}:  # transferencia de liderazgo
                    guild["leader"] = target
                else:  # expulsion
                    guild["members"] = [item for item in members
                                        if str(item.get("player_id")) != target]
                    state["memberships"].pop(target, None)
                _save_state(state)
                return self._base(client, guild, ok)

            if path == "/guild/edit_guild_info":
                if actor != leader:
                    return self._error(ok, 403)
                for request_name, stored_name, limit in (
                    ("name", "name", 32), ("guild_name", "name", 32),
                    ("profile", "profile", 256), ("comment", "profile", 256),
                    ("description", "profile", 256),
                ):
                    if request_name in params:
                        guild[stored_name] = _first(params, request_name)[:limit]
                for names, stored_name in (
                    (("logo", "logo_id"), "logo"), (("language",), "language"),
                    (("play_type",), "play_type"),
                    (("join_type", "approval", "is_approval"), "join_type"),
                ):
                    if any(name in params for name in names):
                        guild[stored_name] = _integer(params, *names)
                _save_state(state)
                return self._base(client, guild, ok)

            if path == "/setting/withdrawal_guild":
                own_guild_id = state["memberships"].get(actor)
                if own_guild_id != guild_id:
                    return self._error(ok, 403)
                if actor == leader:
                    return self._error(ok, 403)
                guild["members"] = [item for item in members
                                    if str(item.get("player_id")) != actor]
                state["memberships"].pop(actor, None)
                _save_state(state)
                return self._base(client, None, ok)

            if path == "/guild/break_guild":
                if actor != leader:
                    return self._error(ok, 403)
                for entry in members:
                    state["memberships"].pop(str(entry.get("player_id", "")), None)
                state["guilds"].pop(str(guild_id), None)
                _save_state(state)
                logging.info("GUILD disuelto id=%s lider=%s", guild_id, actor)
                return self._base(client, None, ok)

            if actor not in state["memberships"]:
                return self._error(ok, 403)
            if state["memberships"].get(actor) != guild_id:
                return self._error(ok, 403)
            if path == "/guild/gj":
                guild["gj"] = int(guild.get("gj", 0)) + 1
            elif path == "/guild/camp_soldier":
                unit_ids = []
                for key, values in params.items():
                    if key.startswith("unit_id"):
                        unit_ids.extend(_integer({key: [value]}, key) for value in values)
                guild["camp_soldier"] = [
                    {"user": actor, "unit_id": unit_id}
                    for unit_id in unit_ids if unit_id > 0
                ][:10]
            elif path == "/guild/return_soldier":
                target_unit = _integer(params, "unit_id")
                guild["camp_soldier"] = [item for item in guild.get("camp_soldier", [])
                                         if not (str(item.get("user")) == actor and
                                                 (not target_unit or int(item.get("unit_id", 0)) == target_unit))]
            _save_state(state)
            payload = self._base(client, guild, ok)
            if path == "/guild/get_guild_bonus":
                payload.update({"item": [], "guild_bonus": []})
            return payload
