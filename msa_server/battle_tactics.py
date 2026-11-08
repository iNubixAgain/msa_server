"""Controlador y persistencia transaccional del modo Battle Tactics."""

from __future__ import annotations

import json
import logging
import random
import threading
import uuid
from datetime import datetime
from typing import Mapping

from . import storage
from .identity import public_player_id
from .inventory import owned_units_payload
from .matchmaking import OnlineMatchmaker
from .profiles import get_profile, profile_detail
from .pvp_history import battle_record, normalise_history
from .storage import atomic_write, client_data_file, client_lock, load_client_json
from .users import ok_payload, user_payload


TACTICS_SEASON = 23
# RoomTactics calls BattleTacticsUtility::getCustomizeChargeMSHouLv, which
# resolves the season again from DateTimeUtility::getNowLocal. Outside the
# bundled January 2023 season it obtains season 0 and dereferences a null
# BattleTacticsUnitAccessor. The native common response parser updates that
# clock BEFORE constructing the battle. Supply a date safely inside season 23
# and its 11:00 term only on battle_start; subsequent ordinary responses restore
# real time. This does not alter the host clock or persisted event timestamps.
TACTICS_BATTLE_COMPAT_TIME = "2023-01-05 11:15:00"
TACTICS_UNIT_MIN = 939
TACTICS_UNIT_MAX = 1152
TACTICS_NORMAL_UNIT_VERSION = 5
TACTICS_SUPPORTER_VERSION = 3
TACTICS_SUPPORTER_COUNT = 2
TACTICS_SUPPORTER_IDS = frozenset({
    1849, 1850, 1851, 1861, 1874, 1889, 1901,
    1913, 1923, 1932, 1943, 1955, 1969, 1979, 1989,
})
DEFAULT_TACTICS_UNITS = [939, 940, 941, 944, 945, 947, 949, 950, 955, 957]
DEFAULT_TACTICS_SUPPORTERS = [1989, 1969]
MAX_HISTORY = 5

# Contrato exacto de m_battle_tactics_shop_contents de MSA 7.13.0.
# ``chance`` representa las compras restantes. limit_num=0 es ilimitado y se
# publica como 99 para que la escena no lo trate como agotado.
TACTICS_SHOP = {
    1: (4917, 1, 500, 0), 2: (4918, 1, 1000, 0),
    3: (4919, 1, 1500, 0), 4: (4920, 1, 3000, 0),
    5: (4921, 1, 6500, 0), 6: (4922, 1, 10000, 0),
    7: (4923, 1, 10000, 0), 8: (4924, 1, 10000, 0),
    9: (4925, 1, 10000, 0), 10: (4926, 1, 10000, 0),
    11: (4855, 10000, 5000, 0), 12: (4855, 50000, 25000, 0),
    13: (4855, 100000, 50000, 0),
    14: (4902, 100, 2000, 1), 15: (4902, 100, 6000, 1),
    16: (4902, 100, 12000, 1), 17: (4902, 100, 18000, 1),
    18: (4902, 100, 30000, 1), 19: (4903, 100, 2000, 1),
    20: (4903, 100, 6000, 1), 21: (4903, 100, 12000, 1),
    22: (4903, 100, 18000, 1), 23: (4903, 100, 30000, 1),
    24: (4904, 100, 2000, 1), 25: (4904, 100, 6000, 1),
    26: (4904, 100, 12000, 1), 27: (4904, 100, 18000, 1),
    28: (4904, 100, 30000, 1),
}
DEFAULT_SHOP_ITEMS = [
    {"id": item_id, "chance": 1 if data[3] else 99}
    for item_id, data in TACTICS_SHOP.items()
]

# Recompensas acumuladas por cantidad de batallas, segun
# m_battle_tactics_time_reward + m_battle_tactics_reward.
TACTICS_TIME_REWARDS = {
    1: [{"item_id": 4894, "num": 1500}],
    5: [{"item_id": 4894, "num": 3000}],
    10: [{"item_id": 4894, "num": 6000}],
    20: [{"item_id": 4894, "num": 6000}],
    30: [{"item_id": 4894, "num": 9000}],
    40: [{"item_id": 4894, "num": 15000}],
    50: [{"item_id": 4894, "num": 30000}],
}

_RESULT_LOCK = threading.RLock()


def default_battle_tactics() -> dict:
    return {
        "season_no": TACTICS_SEASON,
        "battle_num": 0,
        "win": 0,
        "draft_chance": 99,
        "battle_chance": 99,
        "is_draft": 1,
        "coin": 999999,
        "token": f"msa-tactics-{uuid.uuid4().hex[:16]}",
        "deck": {
            "cover": 0,
            "unit_ids": list(DEFAULT_TACTICS_UNITS),
            "deck_attribute": [0] * 10,
        },
        # El parser 7.13.0 lee supporter al mismo nivel que deck. Si se anida
        # dentro de deck, vacia SupporterDeckAccessor antes de iniciar sala.
        "supporter": list(DEFAULT_TACTICS_SUPPORTERS),
        "shop": {"contents": [dict(item) for item in DEFAULT_SHOP_ITEMS]},
        "history": [],
        "reported_rooms": [],
        "claimed_time_rewards": [],
    }


def _normalise_battle_tactics(saved) -> dict:
    base = default_battle_tactics()
    if isinstance(saved, dict):
        base.update(saved)
    base["season_no"] = TACTICS_SEASON
    for key in ("battle_num", "win", "coin"):
        base[key] = max(0, _integer(base.get(key), 0))
    # El servidor privado no restringe Draft ni batallas por dia/horario.
    base["draft_chance"] = 99
    base["battle_chance"] = 99
    base["is_draft"] = 1 if _integer(base.get("is_draft"), 1) else 0
    if not isinstance(base.get("token"), str) or not base["token"]:
        base["token"] = f"msa-tactics-{uuid.uuid4().hex[:16]}"

    shop = base.get("shop")
    if isinstance(shop, list):
        # Migracion transparente del formato usado antes de restaurar el
        # contrato nativo de cliente compatible 7.13.0.
        base["shop"] = {"contents": shop}
    elif not isinstance(shop, dict) or not isinstance(shop.get("contents"), list):
        base["shop"] = {"contents": [dict(item) for item in DEFAULT_SHOP_ITEMS]}
    raw_shop = base["shop"].get("contents", [])
    saved_chances: dict[int, int] = {}
    for raw_item in raw_shop:
        if not isinstance(raw_item, dict):
            continue
        item_id = _integer(raw_item.get("id"), 0)
        if item_id not in TACTICS_SHOP or item_id in saved_chances:
            continue
        limit = TACTICS_SHOP[item_id][3]
        chance = max(0, _integer(raw_item.get("chance"), 0))
        saved_chances[item_id] = min(chance, limit) if limit else 99
    base["shop"] = {"contents": [
        {
            "id": item_id,
            "chance": saved_chances.get(item_id, 1 if data[3] else 99),
        }
        for item_id, data in TACTICS_SHOP.items()
    ]}
    deck = base.get("deck")
    if not isinstance(deck, dict):
        deck = {}
    raw_unit_ids = deck.get("unit_ids")
    unit_ids: list[int] = []
    if isinstance(raw_unit_ids, list):
        for raw_unit_id in raw_unit_ids:
            try:
                unit_id = int(raw_unit_id)
            except (TypeError, ValueError):
                continue
            # La temporada 23 solo publica 939..1152 en
            # m_battle_tactics_unit. IDs normales de Rank (y la antigua 936)
            # se pueden mostrar en la previa, pero no tienen configuración
            # táctica y cierran el cliente al construir RoomTactics.
            if (TACTICS_UNIT_MIN <= unit_id <= TACTICS_UNIT_MAX
                    and unit_id not in unit_ids):
                unit_ids.append(unit_id)
            if len(unit_ids) == 10:
                break
    for unit_id in DEFAULT_TACTICS_UNITS:
        if len(unit_ids) == 10:
            break
        if unit_id not in unit_ids:
            unit_ids.append(unit_id)

    raw_attributes = deck.get("deck_attribute")
    deck_attributes: list[int] = []
    if isinstance(raw_attributes, list):
        for raw_attribute in raw_attributes[:10]:
            try:
                attribute = int(raw_attribute)
                deck_attributes.append(attribute if attribute in (0, 1, 2) else 0)
            except (TypeError, ValueError):
                deck_attributes.append(0)
    deck_attributes.extend([0] * (10 - len(deck_attributes)))

    raw_supporters = (
        saved.get("supporter") if isinstance(saved, dict) else None
    )
    if not isinstance(raw_supporters, list):
        # Migra el formato incorrecto publicado por versiones anteriores.
        raw_supporters = deck.get("supporter")
    supporter_ids: list[int] = []
    if isinstance(raw_supporters, list):
        for raw_supporter in raw_supporters:
            try:
                supporter_id = int(raw_supporter)
            except (TypeError, ValueError):
                continue
            if (supporter_id in TACTICS_SUPPORTER_IDS
                    and supporter_id not in supporter_ids):
                supporter_ids.append(supporter_id)
            if len(supporter_ids) == TACTICS_SUPPORTER_COUNT:
                break
    for supporter_id in DEFAULT_TACTICS_SUPPORTERS:
        if len(supporter_ids) == TACTICS_SUPPORTER_COUNT:
            break
        if supporter_id not in supporter_ids:
            supporter_ids.append(supporter_id)

    deck.pop("supporter", None)
    cover = _integer(deck.get("cover"), 0)
    deck = {
        **deck,
        "cover": cover if cover in (0, 1, 2) else 0,
        "unit_ids": unit_ids,
        "deck_attribute": deck_attributes,
    }
    base["deck"] = deck
    base["supporter"] = supporter_ids
    history = base.get("history", [])
    base["history"] = normalise_history(history, MAX_HISTORY)
    reported = base.get("reported_rooms", [])
    base["reported_rooms"] = (
        [str(room_id) for room_id in reported if room_id][-20:]
        if isinstance(reported, list) else []
    )
    claimed = base.get("claimed_time_rewards", [])
    base["claimed_time_rewards"] = sorted({
        value for value in (_integer(item, 0) for item in claimed)
        if value in TACTICS_TIME_REWARDS
    }) if isinstance(claimed, list) else []
    return base


def _integer(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def battle_tactics_payload(state: dict) -> dict:
    """Expone solamente las claves que parsea BattleTacticsAccessor 7.13.0."""
    return {
        "season_no": state["season_no"],
        "battle_num": state["battle_num"],
        "win": state["win"],
        "draft_chance": state["draft_chance"],
        "battle_chance": state["battle_chance"],
        "is_draft": state["is_draft"],
        "deck": {
            "cover": state["deck"]["cover"],
            "unit_ids": list(state["deck"]["unit_ids"]),
            "deck_attribute": list(state["deck"]["deck_attribute"]),
        },
        "supporter": list(state["supporter"]),
        "shop": {"contents": [
            dict(item) for item in state["shop"]["contents"]
        ]},
        "token": state["token"],
        "coin": state["coin"],
    }


def get_battle_tactics(client: str) -> dict:
    with client_lock(client):
        saved = load_client_json(client, "battle_tactics.json", None)
        state = _normalise_battle_tactics(saved)
        if state != saved:
            save_battle_tactics(client, state)
        return state


def save_battle_tactics(client: str, data: dict) -> None:
    with client_lock(client):
        data["public_id"] = public_player_id(client)
        data["player_snapshot"] = tactics_person(client)
        target = client_data_file(client, "battle_tactics.json")
        atomic_write(target, json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"))


def tactics_person(client: str, default_name: str = "user") -> dict:
    profile = get_profile(client, default_name)
    return {
        "user": public_player_id(client),
        "name": profile["name"],
        "level": 50,
        "avatar_id": profile["avatar"],
        "online_score": 0,
        "type": 1,
        "detail": profile_detail(profile),
    }


def _fixed_unit_param(unit_id: int, source: dict | None = None,
                      supporter: bool = False) -> dict:
    """Construye el estado normalizado de Tactics sin mejoras de cuenta."""
    # Replica MenuLogiU::unit::setUnitInfoStatusLvMax de 7.13.0: nivel 50,
    # evolucion 5 y SceneUnitView::getUnitVersionMax(unit_id). Esta ultima
    # funcion devuelve 3 cuando UnitUtility::isSupporter(unit_id), y 5 para
    # cualquier unidad normal. No depende de los avances de la cuenta.
    level, evolution = 50, 5
    version = (
        TACTICS_SUPPORTER_VERSION if supporter
        else TACTICS_NORMAL_UNIT_VERSION
    )
    return {
        "unit_id": unit_id,
        "level": level,
        "evolution": evolution,
        "version": version,
        "equip": [1] * 6,
        # Los Supporters de 7.13.0 solo definen dos habilidades. Mantener las
        # otras ranuras a cero evita que RoomTactics materialice skills que no
        # existen en los datos maestros.
        "skill": [50, 50, 0, 0] if supporter else [50] * 4,
        "status_seed": [0] * 9,
    }


def tactics_player(identifier: str, slot: int, client: str,
                   tactics_state: dict | None = None) -> dict:
    profile = get_profile(client, f"PLAYER {slot + 1}")
    state = tactics_state or get_battle_tactics(client)
    deck = state.get("deck", {})
    unit_ids = deck.get("unit_ids", DEFAULT_TACTICS_UNITS)
    raw_attributes = deck.get("deck_attribute", [0] * 10)
    supporter_ids = state.get("supporter", DEFAULT_TACTICS_SUPPORTERS)
    cover = deck.get("cover", 0)

    units = owned_units_payload(client).get("unit", [])
    by_id = {entry.get("param", {}).get("unit_id"): entry.get("param", {})
             for entry in units}

    deck_slots = []
    used_unit_ids: set[int] = set()
    fallback_ids = [
        unit_id for unit_id in DEFAULT_TACTICS_UNITS if unit_id in by_id
    ]
    fallback_ids.extend(
        unit_id for unit_id in sorted(by_id)
        if (TACTICS_UNIT_MIN <= unit_id <= TACTICS_UNIT_MAX
            and unit_id not in fallback_ids)
    )
    for index in range(10):
        unit_id = unit_ids[index] if index < len(unit_ids) else -1
        try:
            unit_id = int(unit_id)
        except (TypeError, ValueError):
            unit_id = -1
        attribute = raw_attributes[index] if index < len(raw_attributes) else 0
        try:
            attribute = int(attribute)
        except (TypeError, ValueError):
            attribute = 0
        unit = by_id.get(unit_id)
        if not isinstance(unit, dict):
            replacement = next(
                (candidate for candidate in fallback_ids
                 if candidate not in used_unit_ids),
                fallback_ids[0] if fallback_ids else None,
            )
            unit = by_id.get(replacement)
        if not isinstance(unit, dict):
            # El inventario normal siempre contiene unidades. Este ultimo
            # recurso conserva el contrato aun ante datos totalmente rotos.
            unit = {"unit_id": 939}
        fixed_unit_id = _integer(unit.get("unit_id"), 939)
        used_unit_ids.add(fixed_unit_id)
        deck_slots.append((_fixed_unit_param(fixed_unit_id, unit), attribute))

    unit_param = [unit for unit, _attribute in deck_slots]
    deck_attribute = [attribute for _unit, attribute in deck_slots]
    deployable = [
        unit for unit, attribute in deck_slots
        if attribute == 0
    ]
    supporter_param = []
    for supporter_id in supporter_ids[:TACTICS_SUPPORTER_COUNT]:
        unit = by_id.get(supporter_id)
        supporter_param.append(
            _fixed_unit_param(supporter_id, unit, supporter=True)
        )

    selected_cover = cover if cover in (0, 1, 2) else 0
    return {
        "person": {**tactics_person(client, f"PLAYER {slot + 1}"),
                   "user": identifier},
        "online_score": 0,
        "cover": selected_cover,
        "1on1_win": 0,
        "2on2_win": 0,
        "rank": 1,
        "unit_param": unit_param,
        "deck_attribute": deck_attribute,
        "supporter_param": supporter_param,
        "soldier_unit_param": (deployable or unit_param)[0] if unit_param else {
            "unit_id": 939, "level": 50, "evolution": 5,
            "version": TACTICS_NORMAL_UNIT_VERSION,
            "equip": [1] * 6, "skill": [50] * 4, "status_seed": [0] * 9,
        },
        "build_up": [50] * 11,
        "customize_affiliation": [
            {"type": 1, "level": [50] * 6},
            {"type": 2, "level": [50] * 6},
            {"type": 3, "level": [50] * 6},
        ],
        "base_avatar": profile["base_avatar"],
    }


def _record_history(state: dict, client: str, room: dict | None,
                    result: int, battle_log: str) -> None:
    clients = room.get("clients", []) if isinstance(room, dict) else []
    opponent = next(
        (value for value in clients if value != client), "tactics-cpu-bot"
    )
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["history"].insert(0, battle_record(
        (tactics_person(client), tactics_person(opponent, "RIVAL TACTICS")),
        battle_log, result, timestamp,
    ))
    state["history"] = state["history"][:MAX_HISTORY]


def _ranking_records() -> list[dict]:
    records = []
    root = storage.DATA / "clients"
    if not root.is_dir():
        return records
    for path in root.glob("*/battle_tactics.json"):
        state = _normalise_battle_tactics(storage.read_json_file(path, {}))
        person = state.get("player_snapshot")
        # No publicar estados creados solo como fallback NPC ni instalaciones
        # que aun no disputaron una partida.
        if not isinstance(person, dict) or state["battle_num"] <= 0:
            continue
        records.append({
            "person": person,
            "win": state["win"],
            "battle_num": state["battle_num"],
        })
    records.sort(key=lambda item: (
        -item["win"], item["battle_num"],
        str(item["person"].get("user", "")),
    ))
    for order, record in enumerate(records, 1):
        record["order"] = order
        record.pop("battle_num", None)
    return records[:100]


class BattleTacticsController:
    """Atiende contratos HTTP del modo Battle Tactics."""

    prefix = "/battle_tactics/"

    def __init__(self, matchmaker: OnlineMatchmaker | None = None) -> None:
        self.matchmaker = matchmaker or OnlineMatchmaker()

    def handles(self, path: str) -> bool:
        return path == "/battle_tactics/top" or path.startswith(self.prefix)

    def handle(self, path: str, client: str, params: Mapping[str, list[str]],
               source_ip: str, ok: dict) -> dict | None:
        if not self.handles(path):
            return None

        action = path.rstrip("/").rsplit("/", 1)[-1]
        state = get_battle_tactics(client)

        if action == "top":
            return {
                "user": user_payload(client=client),
                "battle_tactics": battle_tactics_payload(state),
                "response": ok,
            }

        if action == "start_draft":
            state["season_no"] = TACTICS_SEASON
            state["is_draft"] = 0
            state["token"] = f"msa-tactics-{uuid.uuid4().hex[:16]}"
            seed = random.randint(1, 10000000)
            state["draft_seed"] = seed
            state["draft_context"] = ""
            save_battle_tactics(client, state)
            return {
                "battle_tactics": battle_tactics_payload(state),
                "seed": seed,
                "response": ok,
            }

        if action == "end_draft":
            deck_units = self._int_list(params.get("deck_unit[]", []))
            deck_supporters = self._int_list(params.get("deck_supporter[]", []))
            state["season_no"] = TACTICS_SEASON
            state["is_draft"] = 1
            state["draft_context"] = params.get("context", [""])[0]
            if deck_units:
                state["deck"]["unit_ids"] = deck_units[:10]
                state["deck"]["deck_attribute"] = [0] * len(state["deck"]["unit_ids"])
            if deck_supporters:
                state["supporter"] = deck_supporters[:TACTICS_SUPPORTER_COUNT]
            state = _normalise_battle_tactics(state)
            save_battle_tactics(client, state)
            return {
                "battle_tactics": battle_tactics_payload(state),
                "response": ok,
            }

        if action == "set_deck":
            deck_units = self._int_list(params.get("deck_unit[]", []))
            deck_attrs = self._int_list(params.get("deck_attribute[]", []))
            deck_supporters = self._int_list(params.get("deck_supporter[]", []))
            cover_val = self._int_val(params.get("cover", [str(state["deck"].get("cover", 0))])[0], 0)
            if deck_units:
                state["deck"]["unit_ids"] = deck_units[:10]
            if deck_attrs:
                state["deck"]["deck_attribute"] = deck_attrs[:10]
            if deck_supporters:
                state["supporter"] = deck_supporters[:TACTICS_SUPPORTER_COUNT]
            state["deck"]["cover"] = cover_val if cover_val in (0, 1, 2) else 0
            state = _normalise_battle_tactics(state)
            save_battle_tactics(client, state)
            return {
                "battle_tactics": battle_tactics_payload(state),
                "response": ok,
            }

        if action == "create_or_enter_room":
            room = self.matchmaker.enter("tactics", client)
            return {
                "room_id": room["id"],
                "response": ok,
            }

        if action == "decide_room":
            room_id = params.get("room_id", [""])[0]
            room = self.matchmaker.get("tactics", client, room_id)
            cover = self._int_val(
                params.get("cover", [str(state["deck"].get("cover", 0))])[0],
                state["deck"].get("cover", 0),
            )
            if cover in (0, 1, 2):
                state["deck"]["cover"] = cover
                save_battle_tactics(client, state)
            player_no = 1
            if room and client in room["clients"]:
                player_no = room["clients"].index(client) + 1
            logging.info(
                "TACTICS_ROOM_DECIDE room=%s cliente=%s slot=%d cover=%s",
                room_id or "sin-sala", client, player_no, cover,
            )
            return {
                "type": player_no,
                "response": ok,
            }

        if action == "battle_start":
            room_id = params.get("room_id", [""])[0]
            room = self.matchmaker.get("tactics", client, room_id)
            clients = list(room["clients"]) if room else [client]
            while len(clients) < 2:
                clients.append("tactics-cpu-bot")
            players = [
                tactics_player(public_player_id(value), slot, value)
                for slot, value in enumerate(clients[:2])
            ]
            if clients[1] == "tactics-cpu-bot":
                players[1]["person"]["user"] = "msa-tactics-cpu"
                players[1]["person"]["name"] = "CPU TACTICS"
            self.matchmaker.mark_battle_started(
                "tactics", client, room_id
            )
            logging.info(
                "TACTICS_BATTLE_START room=%s solicitante=%s jugadores=%s",
                room_id or "sin-sala", client, clients[:2],
            )
            return {
                "player_info": players,
                "response": {
                    **ok,
                    "now_time": TACTICS_BATTLE_COMPAT_TIME,
                    "server_time": TACTICS_BATTLE_COMPAT_TIME,
                },
            }

        if action == "battle_end":
            room_id = params.get("room_id", [""])[0]
            room = self.matchmaker.get("tactics", client, room_id)
            result = self._int_val(params.get("battle_result", ["1"])[0], 1)
            result = result if result in (1, 2) else 2
            with _RESULT_LOCK, client_lock(client):
                state = get_battle_tactics(client)
                duplicate = bool(
                    room_id and room_id in state["reported_rooms"]
                )
                reward: list[dict] = []
                time_reward: list[dict] = []
                if not duplicate:
                    state["battle_num"] += 1
                    if result == 1:
                        state["win"] += 1
                        reward = [{"item_id": 4855, "num": 2000}]
                    else:
                        reward = [{"item_id": 4855, "num": 1000}]
                    milestone = state["battle_num"]
                    if (milestone in TACTICS_TIME_REWARDS
                            and milestone not in state["claimed_time_rewards"]):
                        time_reward = [
                            dict(item) for item in TACTICS_TIME_REWARDS[milestone]
                        ]
                        state["claimed_time_rewards"].append(milestone)
                        state["coin"] += sum(
                            item["num"] for item in time_reward
                            if item["item_id"] == 4894
                        )
                    if room_id:
                        state["reported_rooms"].append(room_id)
                        state["reported_rooms"] = state["reported_rooms"][-20:]
                    _record_history(
                        state, client, room, result,
                        params.get("battle_log", [""])[0],
                    )
                    save_battle_tactics(client, state)
            return {
                "user": user_payload(client=client),
                "battle_tactics": battle_tactics_payload(state),
                "reward": reward,
                "time_reward": time_reward,
                "response": ok,
            }

        if action == "battle_history":
            return {
                "online_battle_result": state["history"][:MAX_HISTORY],
                "response": ok,
            }

        if action == "leave_room":
            room_id = params.get("room_id", [""])[0]
            self.matchmaker.leave("tactics", client, room_id)
            return ok_payload(ok)

        if action == "ranking_top":
            records = _ranking_records()
            own_id = public_player_id(client)
            own_order = next((
                item["order"] for item in records
                if item["person"].get("user") == own_id
            ), 0)
            return {
                "ranking": [{
                    "season_no": TACTICS_SEASON,
                    "win": state["win"],
                    "order": own_order,
                    "record": records,
                }],
                "response": ok,
            }

        if action == "buy":
            item_id = self._int_val(params.get("id", ["1"])[0], 1)
            content = TACTICS_SHOP.get(item_id)
            shop_item = next((
                item for item in state["shop"]["contents"]
                if item["id"] == item_id
            ), None)
            if content is not None and shop_item is not None:
                _reward_item, _reward_num, price, limit = content
                available = not limit or shop_item["chance"] > 0
                if available and state["coin"] >= price:
                    state["coin"] -= price
                    if limit:
                        shop_item["chance"] -= 1
            save_battle_tactics(client, state)
            return {
                "user": user_payload(client=client),
                "battle_tactics": battle_tactics_payload(state),
                "response": ok,
            }

        return None

    @staticmethod
    def _int_val(val: str, default: int = 0) -> int:
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _int_list(values: list[str]) -> list[int]:
        result: list[int] = []
        for val in values:
            for part in str(val).split(","):
                try:
                    result.append(int(part.strip()))
                except (TypeError, ValueError):
                    continue
        return result
