"""Estado y contratos HTTP del modo Ultimate Mission."""

from __future__ import annotations

from copy import deepcopy
import json

from .content import rental_soldier
from .storage import atomic_write, client_data_file, client_lock, load_client_json
from .users import user_payload


_STATE_FILE = "ultimate_mission.json"
_AFFECTIONS = range(5)


def _default_deck_id(affection: int) -> int:
    """Primer mazo prioritario de la armada asociada al stage."""
    return affection * 10 + 1


def _deck_matches_affection(deck_id: int, affection: int) -> bool:
    first = _default_deck_id(affection)
    return first <= deck_id <= first + 9


def _default_state() -> dict:
    # El cliente compara esta fecha con la hora local. Una fecha vencida hace
    # que muestre "informacion actualizada" y cierre la pantalla del modo.
    return {
        "chance": 3,
        "reset_num": 0,
        "max_reset_num": 3,
        "next_update_time": "2099-12-31 23:59:59",
        "attack_affection": 0,
        "deck": [
            {
                "affection": affection,
                "deck_id": _default_deck_id(affection),
                "rank": 0,
            }
            for affection in _AFFECTIONS
        ],
    }


def _normalise_state(value: object) -> dict:
    default = _default_state()
    if not isinstance(value, dict):
        return default

    state = deepcopy(default)
    for key in ("chance", "reset_num", "max_reset_num", "attack_affection"):
        try:
            state[key] = int(value.get(key, default[key]))
        except (TypeError, ValueError):
            pass

    update_time = value.get("next_update_time")
    if isinstance(update_time, str) and update_time >= "2090-01-01 00:00:00":
        state["next_update_time"] = update_time

    saved_decks = value.get("deck")
    if isinstance(saved_decks, list):
        by_affection = {
            item.get("affection"): item
            for item in saved_decks if isinstance(item, dict)
        }
        for deck in state["deck"]:
            saved = by_affection.get(deck["affection"])
            if not saved:
                continue
            try:
                saved_deck_id = int(saved.get("deck_id", deck["deck_id"]))
                # Las primeras versiones del backend persistieron deck_id=1
                # en las cinco armadas. Eso hacía que todos los stages
                # mostrasen siempre la formación PF. Repara esos estados sin
                # descartar un mazo válido de la armada correspondiente.
                if _deck_matches_affection(
                        saved_deck_id, deck["affection"]):
                    deck["deck_id"] = saved_deck_id
                deck["rank"] = max(0, int(saved.get("rank", 0)))
            except (TypeError, ValueError):
                pass
    return state


class UltimateMissionController:
    """Publica el contrato nativo y conserva el progreso por dispositivo."""

    @staticmethod
    def handles(path: str) -> bool:
        return path.startswith("/ultimate_mission/")

    def _state(self, client: str) -> dict:
        with client_lock(client):
            state = _normalise_state(
                load_client_json(client, _STATE_FILE, _default_state())
            )
            atomic_write(
                client_data_file(client, _STATE_FILE),
                json.dumps(state, separators=(",", ":")).encode("utf-8"),
            )
            return state

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               ok: dict) -> dict | None:
        if not self.handles(path):
            return None

        state = self._state(client)
        user = user_payload(client=client)

        if path == "/ultimate_mission/battle_start":
            return {
                "continue_medal": 0,
                "relief_supplies_contents": {"contents": []},
                "response": ok,
            }

        if path == "/ultimate_mission/battle_continue":
            return {"user": user, "response": ok}

        if path in {"/ultimate_mission/reset_top",
                    "/ultimate_mission/reset_chance"}:
            payload = {"user": user, "response": ok}
            if path.endswith("reset_chance"):
                payload["ultimate_mission"] = state
            return payload

        payload = {
            "user": user,
            "ultimate_mission": state,
            "rental_soldier": rental_soldier(),
            "response": ok,
        }
        if path in {"/ultimate_mission/battle_win",
                    "/ultimate_mission/raid"}:
            payload.update({"rank": 1, "item": []})
        elif path == "/ultimate_mission/battle_lose":
            payload["item"] = []
        return payload
