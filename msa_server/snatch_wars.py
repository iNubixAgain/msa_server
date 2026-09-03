"""Snatch Wars sobre el subsistema Mass conservado por el cliente 7.13.0."""

from __future__ import annotations

import json
import threading

from . import storage
from .guild import guild_event_context
from .users import user_payload


_LOCK = threading.RLock()
_FILE = "snatch_wars.json"
_PARTICIPATING_GUILD_ID = 6
_ROUTES = {
    "/event/mass/entry", "/event/mass/top",
    "/event/mass/battle_start", "/event/mass/battle_end",
    "/event/mass/battle_continue", "/event/mass/build_up",
    "/event/mass/ranking_top", "/event/mass/set_deck",
    "/event/mass/history", "/event/mass/set_tactics",
    "/event/mass/buy_relief_supplies",
}


def _first_int(params: dict[str, list[str]], *keys: str, default: int = 0) -> int:
    for key in keys:
        try:
            return int(params[key][0])
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    return default


def _load() -> dict:
    path = storage.DATA / _FILE
    data = storage.read_json_file(path, {}) if path.is_file() else {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    storage.atomic_write(
        storage.DATA / _FILE,
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(),
    )


class SnatchWarsController:
    def handles(self, path: str) -> bool:
        return path in _ROUTES

    @staticmethod
    def _guild_info(context: dict) -> dict:
        return {
            "guild_id": context["guild_id"], "name": context["name"],
            "avatar_id": context["logo_id"], "language": context["language"],
            "flag": 0,
        }

    def _default_state(self, context: dict) -> dict:
        guild_info = self._guild_info(context)
        rival_info = {
            "guild_id": 900000 + context["guild_id"],
            "name": "SNATCH RIVALS", "avatar_id": 1,
            "language": context["language"], "flag": 1,
        }
        stages = []
        # m_mass_map indexa el tablero desde 1 y solo admite term 1..6 y
        # kind 1..5. Season 11 se representa con el mapa final term 6/kind 1.
        for y in range(1, 6):
            for x in range(1, 6):
                owner = guild_info if (x, y) == (1, 1) else rival_info
                stages.append({
                    "x": x, "y": y, "type": 1,
                    "build_up_msp": 0, "build_up_medal": 0,
                    "stage_situation": [{
                        "guild_info": owner, "damage": 0,
                    }],
                    "chain": 0, "chain_time": "2037-12-31 23:59:59",
                    "guild_info": owner,
                })
        return {
            "schema": 5, "term_id": 6, "kind": 1,
            "participating_guild_id": _PARTICIPATING_GUILD_ID,
            "stage": stages,
            "news": [], "deck": [], "cover": 0, "unit_ids": [],
            "role": context["role"], "tactics": 0, "can_reward": 0,
            "score": 0, "stamina": 100,
        }

    def _mass(self, client: str, context: dict, state: dict) -> dict:
        guild_info = self._guild_info(context)
        # Coincide exactamente con JsonParseSupporter::parseMass. El estado
        # interno (schema, stamina, score) nunca se filtra al cliente.
        result = {
            key: state[key] for key in (
                "term_id", "kind", "stage", "news", "deck", "cover",
                "unit_ids", "role", "tactics", "can_reward",
            )
        }
        result["guild_info"] = guild_info
        # `person` usa el modelo nativo Person completo. Omitir una persona
        # incompleta permite que parseMassOwnStage produzca una lista vacia
        # valida, sin descartar la carga completa del tablero.
        result["own_stage"] = []
        return result

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               ok: dict) -> dict | None:
        if not self.handles(path):
            return None
        context = guild_event_context(client)
        if (context is None
                or int(context.get("guild_id", 0)) != _PARTICIPATING_GUILD_ID):
            response = dict(ok); response["error_code"] = 404
            return {"status_code": 0, "response": response}
        key = str(_PARTICIPATING_GUILD_ID)
        with _LOCK:
            all_state = _load()
            state = all_state.get(key)
            if (not isinstance(state, dict)
                    or not isinstance(state.get("stage"), list)
                    or state.get("schema") != 5
                    or int(state.get("participating_guild_id", 0))
                       != _PARTICIPATING_GUILD_ID):
                state = self._default_state(context)
                all_state[key] = state

            if path == "/event/mass/build_up":
                x = _first_int(params, "x", "x[]")
                y = _first_int(params, "y", "y[]")
                for tile in state["stage"]:
                    if tile["x"] == x and tile["y"] == y:
                        tile["build_up_msp"] = int(tile["build_up_msp"]) + 1
            elif path == "/event/mass/battle_end":
                state["score"] = int(state.get("score", 0)) + max(
                    1, _first_int(params, "damage", "score", default=1)
                )
                state["stamina"] = min(100, int(state.get("stamina", 100)) + 10)
            elif path == "/event/mass/battle_start":
                state["stamina"] = max(0, int(state.get("stamina", 100)) - 10)
            elif path == "/event/mass/set_tactics":
                state["tactics"] = _first_int(params, "tactics", "type")
            _save(all_state)

            payload = {
                "user": user_payload(client=client),
                "mass": self._mass(client, context, state),
                "status_code": 1, "response": ok,
            }
            if path in {"/event/mass/battle_start", "/event/mass/battle_continue"}:
                payload.update({
                    "continue_medal": 0,
                    "relief_supplies_contents": {"contents": []},
                    "seec": 0,
                })
            if path == "/event/mass/battle_end":
                payload.update({"is_my_chain": 1, "item": [], "reward": []})
            if path == "/event/mass/history":
                payload["mass_battle_result"] = []
            if path == "/event/mass/ranking_top":
                payload.update({"ranking_data": [], "last_term_id": 0})
            return payload
