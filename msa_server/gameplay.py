"""Controlador modular de las familias offline y de gameplay confirmado."""

from __future__ import annotations

from .content import rental_soldier, story_payload, world_map_payload
from .decks import persist_decks
from .inventory import upgrade_status_seeds
from .storage import read_json_file, response_file
from .users import ok_payload, user_only, user_payload


def marathon_payload() -> dict:
    """Estado minimo valido que consume SceneEvtMarathon del cliente."""
    worlds = []
    first_stage_id = 0x7E6E
    for world_index in range(3):
        stages = []
        for stage_index in range(10):
            stage_id = first_stage_id + world_index * 10 + stage_index
            stages.append({
                "stage_id": stage_id,
                "state": 1 if stage_id == first_stage_id else 0,
                "rank": 0,
            })
        worlds.append({"kind": world_index + 1, "stage": stages})
    return {
        "world": worlds,
        "event_point": 0,
        "is_tutorial": 1,
        "is_quest_reward": 0,
    }


class GameplayController:
    """Construye contratos de gameplay sin conocer el transporte HTTP."""

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               ok: dict) -> dict | None:
        if path in {"/build_up/top", "/build_up/set_deck", "/build_up/up",
                    "/build_up/buy_deck", "/build_up/customize_affiliation_up"}:
            if path == "/build_up/set_deck":
                persist_decks(params, client)
            return user_only(ok, client)
        if path == "/unit_up/top":
            return {"user": user_payload(client=client), "stage": [], "response": ok}
        if path == "/unit_up/status_seed_up":
            upgrade_status_seeds(
                client,
                self._first_int(params, "unit_id"),
                self._int_values(params.get("type[]", [])),
                self._int_values(params.get("up_level[]", [])),
            )
            return user_only(ok, client)
        if path in {"/unit_up/compose", "/unit_up/equip", "/unit_up/evolution",
                    "/unit_up/level_up", "/unit_up/skill_up",
                    "/unit_up/version_up"}:
            return user_only(ok, client)

        if path in {"/sneak_shop/top", "/sneak_shop/buy", "/sneak_shop/update",
                    "/guild_shop/top", "/guild_shop/buy", "/guild_shop/update",
                    "/stamina_shop/top", "/stamina_shop/buy"}:
            return {"user": user_payload(client=client),
                    "info": {"contents": [],
                             "next_update_time": "2030-12-31 23:59:00"},
                    "response": ok}

        if path == "/world_map/top":
            return {"user": user_payload(client=client),
                    "world_map": world_map_payload(),
                    "rental_soldier": rental_soldier(),
                    "weekboss_reset_time": "2023-01-12 15:30:00", "response": ok}
        if path == "/world_map/battle_start":
            return {"continue_medal": 0,
                    "relief_supplies_contents": {"contents": []}, "response": ok}
        if path == "/world_map/reset_top":
            return {"user": user_payload(client=client),
                    "info": {"money": 0, "price": 0},
                    "recovery": 0, "response": ok}
        if path.startswith("/world_map/"):
            return {"user": user_payload(client=client),
                    "world_map": world_map_payload(),
                    "rental_soldier": rental_soldier(), "pp": 0,
                    "add_beginner_pp": 0, "add_campaign_pp": 0, "msp": 0,
                    "add_msp": 0, "rank": 1, "item": [], "item_first": [],
                    "response": ok}
        if path == "/story/top":
            return {"user": user_payload(client=client),
                    "story": story_payload(), "response": ok}
        if path == "/story/battle_start":
            return {"continue_medal": 0,
                    "relief_supplies_contents": {"contents": []}, "response": ok}
        if path.startswith("/story/"):
            return {"user": user_payload(client=client),
                    "story": story_payload(), "pp": 0,
                    "add_beginner_pp": 0, "add_campaign_pp": 0, "msp": 0,
                    "add_msp": 0, "rank": 1, "item": [], "item_first": [],
                    "item_all_s_rank": [], "response": ok}

        if path == "/practice_1on1/battle_start":
            target = response_file("practice_1on1_start.json")
            if target:
                payload = read_json_file(target, {})
                payload["response"] = ok
                return payload
        if path in {"/practice_1on1/battle_win", "/practice_1on1/battle_lose",
                    "/labo/battle_end", "/practice_own/battle_end"}:
            return ok_payload(ok)
        if path == "/labo/history":
            return {"labo_result": [], "response": ok}
        if path == "/practice_own/history":
            return {"practice_own_result": [], "response": ok}

        if path.startswith("/search/"):
            return {"user": user_payload(client=client), "search": {},
                    "search_point": [], "response": ok}
        if path.startswith("/pow/"):
            payload = {"user": user_payload(client=client), "pow": {},
                       "response": ok}
            if path.endswith("battle_start"):
                payload.update({"continue_medal": 0,
                                "relief_supplies_contents": {"contents": []}})
            return payload
        if path.startswith("/boot_camp/") or path.startswith("/training/"):
            key = "boot_camp" if path.startswith("/boot_camp/") else "training"
            return {"user": user_payload(client=client), key: {}, "stage": [],
                    "reward": [], "rental_soldier": rental_soldier(),
                    "response": ok}
        if path.startswith(("/event/marathon/", "/event/marathon2nd/")):
            payload = {
                "user": user_payload(client=client),
                "marathon": marathon_payload(),
                "rental_soldier": rental_soldier(),
                "response": ok,
            }
            if path.endswith(("battle_win", "battle_lose", "battle_retire",
                              "raid", "battle_continue")):
                payload.update({
                    "pp": 0, "add_beginner_pp": 0,
                    "add_campaign_pp": 0, "event_point": 0,
                    "add_rank_event_point": 0,
                    "add_sp_unit_event_point": 0,
                    "add_item_event_point": 0, "rank": 1,
                    "item": [], "item_first": [],
                    "item_all_s_rank": [], "point_reward": {},
                })
            return payload
        if path.startswith(("/off_1on1/", "/off_team/")):
            mode = "off_1on1" if path.startswith("/off_1on1/") else "off_team"
            return {"user": user_payload(client=client), mode: {}, "enemy": {},
                    "battle_log": "", "result": 1, "response": ok}
        if path.startswith(("/off_1on1_btl_shop/", "/off_team_btl_shop/")):
            return {"user": user_payload(client=client),
                    "info": {"contents": [],
                             "next_update_time": "2030-12-31 23:59:00"},
                    "response": ok}

        event_prefixes = {
            "/event/recapture/": "recapture", "/event/score_attack/": "score_attack",
            "/event/tforce/": "tforce", "/event/tline/": "tline",
            "/event/united/": "united", "/event/extension/": "extension",
            "/event/mass/": "mass", "/event/pile/": "pile",
        }
        for event_prefix, event_key in event_prefixes.items():
            if path.startswith(event_prefix):
                return {"user": user_payload(client=client), event_key: {},
                        "item": [], "ranking_data": [], "response": ok}

        compatible_prefixes = (
            "/avatar/", "/customize/", "/daily_quest/", "/dress_up/",
            "/infiltrate/", "/item/", "/loto/",
            "/mail/", "/present/", "/profile/", "/quest/", "/ranking/",
            "/shop/", "/support/", "/top_menu/", "/tutorial/",
        )
        if path.startswith(compatible_prefixes):
            return user_only(ok, client)
        return None

    @staticmethod
    def _first_int(params: dict[str, list[str]], name: str) -> int:
        try:
            values = params.get(name, [])
            return int(values[0]) if values else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _int_values(values: list[str]) -> list[int]:
        result = []
        for value in values:
            try:
                result.append(int(value))
            except (TypeError, ValueError):
                return []
        return result
