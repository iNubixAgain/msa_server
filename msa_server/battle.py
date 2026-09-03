"""Battle asíncrono 1v1 contra defensas persistentes de jugadores o NPC."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from . import storage
from .decks import load_decks
from .identity import public_player_id
from .profiles import get_profile, profile_detail
from .users import user_payload


_LOCK = threading.RLock()
_LADDER_FILE = "battle_ladder.json"
_STATE_FILE = "battle.json"
_UNLIMITED_CHANCE_DISPLAY = 5
_WIN_POINTS = 100
_LOSS_POINTS = 25
_MAX_HISTORY = 20

_NPCS = (
    ("msa-battle-npc-iron", "Iron Comet", 2356, 0,
     (16, 17, 18, 19, 27, 2, 3, 6, 7, 8)),
    ("msa-battle-npc-ruby", "Ruby Vanguard", 2368, 1,
     (15, 16, 17, 18, 19, 20, 21, 22, 23, 27)),
    ("msa-battle-npc-blue", "Blue Harbor", 2381, 2,
     (2, 3, 6, 7, 8, 11, 12, 13, 14, 25)),
    ("msa-battle-npc-mars", "Mars Patrol", 2394, 0,
     (106, 109, 110, 111, 113, 114, 115, 118, 119, 121)),
    ("msa-battle-npc-rebel", "Rebel Guard", 2407, 1,
     (24, 38, 39, 40, 41, 43, 44, 47, 50, 69)),
    ("msa-battle-npc-elite", "Elite Fortress", 2420, 2,
     (34, 35, 49, 52, 53, 55, 61, 62, 87, 88)),
)


def _integer(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_values(values, limit: int = 10) -> list[int]:
    result: list[int] = []
    for raw in values or []:
        for value in str(raw).split(","):
            if len(result) >= limit:
                return result
            try:
                result.append(int(value))
            except ValueError:
                continue
    return result


def _default_deck(client: str) -> dict:
    decks = load_decks(client)
    raw = decks[3] if len(decks) > 3 and isinstance(decks[3], dict) else {}
    units = [_integer(value, -1) for value in raw.get("unit_ids", [])[:10]]
    units = [value for value in units if value > 0]
    if not units:
        units = list(_NPCS[0][4])
    attributes = [_integer(value) for value in raw.get("deck_attribute", [])[:10]]
    attributes.extend([0] * (len(units) - len(attributes)))
    return {
        "cover": max(0, min(2, _integer(raw.get("cover", 0)))),
        "unit_ids": units[:10],
        "deck_attribute": attributes[:len(units)],
    }


def _decks_from_slots(client: str, first: int) -> list[dict]:
    decks = load_decks(client)
    result = []
    for index in range(first, first + 3):
        raw = decks[index] if len(decks) > index and isinstance(decks[index], dict) else {}
        units = [_integer(value, -1) for value in raw.get("unit_ids", [])[:10]]
        units = [value for value in units if value > 0] or list(_NPCS[index % len(_NPCS)][4])
        attributes = [_integer(value) for value in raw.get("deck_attribute", [])[:10]]
        attributes.extend([0] * (len(units) - len(attributes)))
        result.append({
            "cover": max(0, min(2, _integer(raw.get("cover", 0)))),
            "unit_ids": units, "deck_attribute": attributes[:len(units)],
        })
    return result


def _default_state(client: str) -> dict:
    return {
        "version": 1,
        "score": 0,
        "wins": 0,
        "losses": 0,
        "max_rank": 1000,
        "defence_deck": _default_deck(client),
        "opponent_cursor": 0,
        "current_opponent": "",
        "pending_token": "",
        "settled_tokens": [],
        "history": [],
        "team": {
            "score": 0, "wins": 0, "losses": 0, "max_rank": 1000,
            "defence_decks": _decks_from_slots(client, 4),
            "offence_decks": _decks_from_slots(client, 7),
            "opponent_cursor": 0, "current_opponent": "",
            "pending_token": "", "settled_tokens": [], "history": [],
        },
    }


def _load_state(client: str) -> dict:
    fallback = _default_state(client)
    state = storage.load_client_json(client, _STATE_FILE, fallback)
    if not isinstance(state, dict):
        state = fallback
    for key, value in fallback.items():
        state.setdefault(key, value)
    state["score"] = max(0, _integer(state.get("score")))
    state["wins"] = max(0, _integer(state.get("wins")))
    state["losses"] = max(0, _integer(state.get("losses")))
    state["history"] = [entry for entry in state.get("history", [])
                        if isinstance(entry, dict)][:_MAX_HISTORY]
    state["settled_tokens"] = [str(value) for value in
                               state.get("settled_tokens", [])][-20:]
    team_default = fallback["team"]
    team = state.get("team")
    if not isinstance(team, dict):
        team = {}
        state["team"] = team
    for key, value in team_default.items():
        team.setdefault(key, value)
    for key in ("score", "wins", "losses"):
        team[key] = max(0, _integer(team.get(key)))
    team["history"] = [entry for entry in team.get("history", [])
                       if isinstance(entry, dict)][:_MAX_HISTORY]
    team["settled_tokens"] = [str(value) for value in
                              team.get("settled_tokens", [])][-20:]
    return state


def _save_state(client: str, state: dict) -> None:
    storage.atomic_write(
        storage.client_data_file(client, _STATE_FILE),
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _load_ladder() -> dict:
    path = storage.DATA / _LADDER_FILE
    raw = storage.read_json_file(path, {}) if path.is_file() else {}
    if not isinstance(raw, dict) or not isinstance(raw.get("players", {}), dict):
        return {"version": 1, "players": {}}
    raw["version"] = 1
    raw.setdefault("players", {})
    return raw


def _save_ladder(ladder: dict) -> None:
    storage.atomic_write(
        storage.DATA / _LADDER_FILE,
        json.dumps(ladder, ensure_ascii=False, separators=(",", ":")).encode(),
    )


def _profile_snapshot(client: str) -> dict:
    profile = get_profile(client, "PLAYER")
    return {
        "name": profile["name"], "avatar": profile["avatar"],
        "language": profile["language"], "profile": profile["profile"],
        "play_type": profile["play_type"], "is_accept": profile["is_accept"],
        "base_avatar": profile["base_avatar"],
    }


def _register(client: str, state: dict, ladder: dict) -> dict:
    player_id = public_player_id(client)
    entry = ladder["players"].setdefault(player_id, {})
    entry.update({
        "public_id": player_id,
        "profile": _profile_snapshot(client),
        "score": state["score"], "wins": state["wins"],
        "losses": state["losses"],
        "defence_deck": state["defence_deck"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    team = state.get("team", {})
    if isinstance(team, dict):
        entry.update({
            "team_score": max(0, _integer(team.get("score"))),
            "team_wins": max(0, _integer(team.get("wins"))),
            "team_losses": max(0, _integer(team.get("losses"))),
            "team_defence_decks": team.get("defence_decks", []),
        })
    return entry


def _ranked_players(ladder: dict) -> list[dict]:
    players = [entry for entry in ladder["players"].values()
               if isinstance(entry, dict)]
    return sorted(players, key=lambda entry: (
        -_integer(entry.get("score")), -_integer(entry.get("wins")),
        str(entry.get("public_id", "")),
    ))


def _rank_for(player_id: str, ladder: dict) -> int:
    for index, entry in enumerate(_ranked_players(ladder), 1):
        if entry.get("public_id") == player_id:
            return index
    return max(1, len(ladder["players"]) + 1)


def _npc(index: int, score: int) -> dict:
    player_id, name, avatar, cover, units = _NPCS[index % len(_NPCS)]
    return {
        "public_id": player_id, "profile": {
            "name": name, "avatar": avatar, "language": 8,
            "profile": "CPU Battle", "play_type": 0, "is_accept": 0,
            "base_avatar": 0,
        },
        "score": max(0, score), "wins": 10 + index * 3, "losses": 0,
        "defence_deck": {"cover": cover, "unit_ids": list(units),
                         "deck_attribute": [0] * 10},
        "npc_rank": max(1, 1000 - index * 50),
    }


def _candidates(client: str, state: dict, ladder: dict) -> list[dict]:
    own_id = public_player_id(client)
    real = [entry for entry in _ranked_players(ladder)
            if entry.get("public_id") != own_id]
    real.sort(key=lambda entry: abs(_integer(entry.get("score")) - state["score"]))
    npcs = [_npc(index, max(0, state["score"] + (index - 2) * 50))
            for index in range(len(_NPCS))]
    return (real[:10] + npcs) or npcs


def _select_opponent(client: str, state: dict, ladder: dict,
                     rotate: bool = False) -> dict:
    candidates = _candidates(client, state, ladder)
    if rotate:
        state["opponent_cursor"] = _integer(state.get("opponent_cursor")) + 1
    wanted = str(state.get("current_opponent", ""))
    opponent = next((entry for entry in candidates
                     if entry.get("public_id") == wanted), None)
    if opponent is None or rotate:
        opponent = candidates[state["opponent_cursor"] % len(candidates)]
        state["current_opponent"] = opponent["public_id"]
    return opponent


def _opponent_choices(client: str, state: dict, ladder: dict,
                      current: dict) -> list[dict]:
    """Devuelve los tres puestos que la pantalla Battle dibuja."""
    candidates = _candidates(client, state, ladder)
    ordered = [current] + [entry for entry in candidates
                           if entry.get("public_id") != current.get("public_id")]
    return ordered[:3]


def _battle_user_payload(client: str, state: dict) -> dict:
    """Inyecta los mazos Battle guardados en los slots nativos del usuario."""
    payload = user_payload(client=client)
    decks = payload.get("deck", [])
    if not isinstance(decks, list):
        decks = []
    while len(decks) < 10:
        decks.append({"cover": 0, "unit_ids": [-1] * 10,
                      "deck_attribute": [0] * 10})
    decks[3] = dict(state["defence_deck"])
    team = state.get("team", {}) if isinstance(state.get("team"), dict) else {}
    for offset, deck in enumerate(team.get("defence_decks", [])[:3]):
        decks[4 + offset] = dict(deck)
    for offset, deck in enumerate(team.get("offence_decks", [])[:3]):
        decks[7 + offset] = dict(deck)
    payload["deck"] = decks
    return payload


def _person(entry: dict) -> dict:
    profile = entry.get("profile", {})
    normalized = {
        "name": str(profile.get("name", "RIVAL")),
        "avatar": _integer(profile.get("avatar", 1), 1),
        "language": _integer(profile.get("language", 8), 8),
        "profile": str(profile.get("profile", "Battle")),
        "play_type": _integer(profile.get("play_type")),
        "is_accept": _integer(profile.get("is_accept")),
        "base_avatar": _integer(profile.get("base_avatar")),
    }
    return {
        "user": str(entry.get("public_id", "msa-battle-npc")),
        "name": normalized["name"], "level": 50,
        "avatar_id": normalized["avatar"],
        "online_score": _integer(entry.get("score")), "type": 0,
        "detail": profile_detail(normalized),
    }


def _opponent_payload(entry: dict, ladder: dict) -> dict:
    player_id = str(entry.get("public_id", ""))
    rank = (_integer(entry.get("npc_rank"), 1000)
            if player_id.startswith("msa-battle-npc-")
            else _rank_for(player_id, ladder))
    return {
        "person": _person(entry), "rank": rank, "max_rank": rank,
        "win": _integer(entry.get("wins")),
        "total_win": _integer(entry.get("wins")),
    }


def _unit_param(unit_id: int) -> dict:
    return {
        "unit_id": unit_id, "level": 50, "evolution": 5, "version": 5,
        "equip": [1] * 6, "skill": [50] * 4,
        "status_seed": [10, 10, 10, 10, 0, 0, 0, 0, 0],
    }


def _enemy_deck(entry: dict) -> dict:
    deck = entry.get("defence_deck", {})
    units = [_integer(value, -1) for value in deck.get("unit_ids", [])[:10]]
    units = [value for value in units if value > 0]
    attributes = [_integer(value) for value in deck.get("deck_attribute", [])[:10]]
    attributes.extend([0] * (len(units) - len(attributes)))
    return {
        "cover": max(0, min(2, _integer(deck.get("cover", 0)))),
        "unit_param": [_unit_param(unit_id) for unit_id in units],
        "deck_attribute": attributes[:len(units)],
    }


def _mode_payload(client: str, state: dict, ladder: dict,
                  opponent: dict) -> dict:
    player_id = public_player_id(client)
    rank = _rank_for(player_id, ladder)
    state["max_rank"] = min(max(1, _integer(state.get("max_rank"), rank)), rank)
    return {
        # El valor nunca disminuye: los intentos son ilimitados.
        "chance": _UNLIMITED_CHANCE_DISPLAY,
        "reset_num": 0, "max_reset_num": 20,
        "rank": rank, "win_rank": rank, "max_rank": state["max_rank"],
        "win": state["wins"], "total_win": state["wins"],
        "battle_point": state["score"],
        "result_history": state["history"],
        "opponent": [_opponent_payload(entry, ladder) for entry in
                     _opponent_choices(client, state, ladder, opponent)],
    }


class BattleController:
    """Contrato de `/off_1on1/` autoritativo y completamente server-side."""

    prefix = "/off_1on1/"

    def handles(self, path: str) -> bool:
        return path == "/off_1on1/top" or path.startswith(self.prefix)

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               ok: dict) -> dict | None:
        if not self.handles(path):
            return None
        action = path.rsplit("/", 1)[-1]
        with _LOCK, storage.client_lock(client):
            state = _load_state(client)
            ladder = _load_ladder()
            _register(client, state, ladder)
            opponent = _select_opponent(
                client, state, ladder, rotate=action == "get_enemy"
            )

            if action == "set_defence_deck":
                units = _int_values(params.get("deck_unit[]", []))
                attributes = _int_values(params.get("deck_attribute[]", []))
                if units:
                    attributes.extend([0] * (len(units) - len(attributes)))
                    state["defence_deck"] = {
                        "cover": max(0, min(2, _integer(
                            params.get("cover", [0])[0]
                        ))),
                        "unit_ids": units, "deck_attribute": attributes[:len(units)],
                    }
                    _register(client, state, ladder)
                payload = {"user": _battle_user_payload(client, state)}
            elif action == "battle_start":
                requested = str(params.get("enemy_id", [""])[0])
                candidates = _candidates(client, state, ladder)
                opponent = next((entry for entry in candidates
                                 if entry.get("public_id") == requested), opponent)
                state["current_opponent"] = opponent["public_id"]
                state["pending_token"] = "msa-battle-" + uuid.uuid4().hex
                payload = {
                    "enemy_deck": _enemy_deck(opponent),
                    "build_up": [50] * 11,
                    "customize_affiliation": [
                        {"type": 1, "level": [50] * 6},
                        {"type": 2, "level": [50] * 6},
                        {"type": 3, "level": [50] * 6},
                    ],
                    "islock": 0,
                    "rank": _opponent_payload(opponent, ladder)["rank"],
                    "token": state["pending_token"],
                    "base_avatar": _integer(
                        opponent.get("profile", {}).get("base_avatar", 0)
                    ),
                }
                logging.info("BATTLE_CPU_START cliente=%s rival=%s",
                             client, opponent["public_id"])
            elif action in {"battle_win", "battle_lose"}:
                token = str(params.get("token", [state.get("pending_token", "")])[0])
                already_settled = token and token in state["settled_tokens"]
                if not already_settled:
                    won = action == "battle_win"
                    if won:
                        state["score"] += _WIN_POINTS
                        state["wins"] += 1
                    else:
                        state["score"] = max(0, state["score"] - _LOSS_POINTS)
                        state["losses"] += 1
                    history = {
                        "person": _person(opponent),
                        "result": 1 if won else 2,
                        "is_offense": 1,
                        "total_win": state["wins"],
                        "battle_time": datetime.now(timezone.utc).isoformat(),
                    }
                    state["history"].insert(0, history)
                    state["history"] = state["history"][:_MAX_HISTORY]
                    if token:
                        state["settled_tokens"].append(token)
                        state["settled_tokens"] = state["settled_tokens"][-20:]
                    state["pending_token"] = ""
                    _register(client, state, ladder)
                    logging.info(
                        "BATTLE_CPU_END cliente=%s rival=%s resultado=%s puntos=%d",
                        client, opponent["public_id"], "win" if won else "lose",
                        state["score"],
                    )
                payload = ({"user": _battle_user_payload(client, state)}
                           if action == "battle_win" else {})
                if action == "battle_win":
                    payload["reward"] = []
            elif action == "ranking":
                kind = _integer(params.get("kind", [1])[0], 1)
                ranked = _ranked_players(ladder)[:50]
                payload = {
                    "my_rank": (_rank_for(public_player_id(client), ladder)
                                if kind < 3 else max(1, state["wins"])),
                    "online_score": state["score"],
                    "top50": [_person(entry) for entry in ranked],
                }
                _save_state(client, state)
                _save_ladder(ladder)
                payload["response"] = ok
                return payload
            elif action in {"reset_top", "reset_chance"}:
                # Compatibilidad: reponer es gratuito y no cambia la cantidad.
                payload = {}
            elif action not in {"top", "get_enemy"}:
                return None
            else:
                payload = {}

            _register(client, state, ladder)
            opponent = _select_opponent(client, state, ladder)
            if action == "get_enemy":
                payload["opponent"] = [
                    _opponent_payload(entry, ladder) for entry in
                    _opponent_choices(client, state, ladder, opponent)
                ]
            elif action != "battle_start":
                payload["off_1on1_btl"] = _mode_payload(
                    client, state, ladder, opponent
                )
            if action in {"top", "set_defence_deck", "battle_win"}:
                payload.setdefault("user", _battle_user_payload(client, state))
            if action == "top":
                payload.update({
                    "daily_time": "2037-12-31 23:59:59",
                    "monthly_time": "2037-12-31 23:59:59",
                    "other_rank": 0,
                })
            _save_state(client, state)
            _save_ladder(ladder)
            payload["response"] = ok
            return payload


def _team_virtual_ladder(ladder: dict) -> dict:
    players = {}
    for player_id, entry in ladder.get("players", {}).items():
        if not isinstance(entry, dict):
            continue
        view = dict(entry)
        decks = entry.get("team_defence_decks", [])
        view.update({
            "score": _integer(entry.get("team_score")),
            "wins": _integer(entry.get("team_wins")),
            "losses": _integer(entry.get("team_losses")),
            "defence_deck": decks[0] if isinstance(decks, list) and decks else
                            entry.get("defence_deck", {}),
            "team_decks": decks if isinstance(decks, list) else [],
        })
        players[player_id] = view
    return {"version": 1, "players": players}


def _team_enemy_decks(entry: dict) -> list[dict]:
    decks = entry.get("team_decks", [])
    if isinstance(decks, list) and len(decks) >= 3:
        return [_enemy_deck({"defence_deck": deck}) for deck in decks[:3]]
    player_id = str(entry.get("public_id", ""))
    base = next((index for index, npc in enumerate(_NPCS)
                 if npc[0] == player_id), 0)
    return [_enemy_deck(_npc(base + offset, _integer(entry.get("score"))))
            for offset in range(3)]


def _submitted_team_decks(params: dict[str, list[str]]) -> list[dict]:
    decks = []
    for slot in range(1, 4):
        units = _int_values(params.get(f"deck_unit_{slot}[]", []))
        attributes = _int_values(params.get(f"deck_attribute_{slot}[]", []))
        if not units:
            return []
        attributes.extend([0] * (len(units) - len(attributes)))
        decks.append({
            "cover": max(0, min(2, _integer(
                params.get(f"cover_{slot}", [0])[0]
            ))),
            "unit_ids": units,
            "deck_attribute": attributes[:len(units)],
        })
    return decks


class TeamBattleController:
    """Team Battle asíncrono: tres mazos ofensivos contra tres defensivos."""

    prefix = "/off_team/"

    def handles(self, path: str) -> bool:
        return path == "/off_team/top" or path.startswith(self.prefix)

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               ok: dict) -> dict | None:
        if not self.handles(path):
            return None
        action = path.rsplit("/", 1)[-1]
        with _LOCK, storage.client_lock(client):
            state = _load_state(client)
            team = state["team"]
            ladder = _load_ladder()
            _register(client, state, ladder)
            virtual = _team_virtual_ladder(ladder)
            opponent = _select_opponent(
                client, team, virtual, rotate=action == "get_enemy"
            )

            requested = str(params.get("enemy_id", [""])[0])
            if requested:
                opponent = next((entry for entry in _candidates(client, team, virtual)
                                 if entry.get("public_id") == requested), opponent)
                team["current_opponent"] = opponent["public_id"]

            if action in {"set_defence_deck", "set_offence_deck"}:
                decks = _submitted_team_decks(params)
                if decks:
                    team["defence_decks" if action == "set_defence_deck"
                         else "offence_decks"] = decks
                    _register(client, state, ladder)
                    virtual = _team_virtual_ladder(ladder)
                payload = {"user": _battle_user_payload(client, state)}
            elif action == "prepare_battle_start":
                payload = {
                    "enemy_deck": _team_enemy_decks(opponent),
                    "build_up": [50] * 11,
                    "customize_affiliation": [
                        {"type": 1, "level": [50] * 6},
                        {"type": 2, "level": [50] * 6},
                        {"type": 3, "level": [50] * 6},
                    ],
                }
            elif action == "battle_start":
                team["pending_token"] = "msa-team-battle-" + uuid.uuid4().hex
                payload = {
                    "islock": 0,
                    "rank": _opponent_payload(opponent, virtual)["rank"],
                    "token": team["pending_token"],
                }
                logging.info("TEAM_BATTLE_CPU_START cliente=%s rival=%s",
                             client, opponent["public_id"])
            elif action in {"battle_win", "battle_lose"}:
                token = str(params.get("token", [team.get("pending_token", "")])[0])
                if not (token and token in team["settled_tokens"]):
                    won = action == "battle_win"
                    if won:
                        team["score"] += _WIN_POINTS
                        team["wins"] += 1
                    else:
                        team["score"] = max(0, team["score"] - _LOSS_POINTS)
                        team["losses"] += 1
                    team["history"].insert(0, {
                        "person": _person(opponent), "result": 1 if won else 2,
                        "is_offense": 1, "total_win": team["wins"],
                        "battle_time": datetime.now(timezone.utc).isoformat(),
                    })
                    team["history"] = team["history"][:_MAX_HISTORY]
                    if token:
                        team["settled_tokens"].append(token)
                        team["settled_tokens"] = team["settled_tokens"][-20:]
                    team["pending_token"] = ""
                    _register(client, state, ladder)
                    virtual = _team_virtual_ladder(ladder)
                payload = ({"user": _battle_user_payload(client, state),
                            "reward": []} if action == "battle_win" else {})
            elif action == "ranking":
                ranked = _ranked_players(virtual)[:50]
                payload = {
                    "my_rank": _rank_for(public_player_id(client), virtual),
                    "online_score": team["score"],
                    "top50": [_person(entry) for entry in ranked],
                    "response": ok,
                }
                _save_state(client, state)
                _save_ladder(ladder)
                return payload
            elif action in {"reset_top", "reset_chance", "battle_prolong"}:
                payload = {}
            elif action not in {"top", "get_enemy"}:
                return None
            else:
                payload = {}

            _register(client, state, ladder)
            virtual = _team_virtual_ladder(ladder)
            opponent = _select_opponent(client, team, virtual)
            if action == "get_enemy":
                payload["opponent"] = [
                    _opponent_payload(entry, virtual) for entry in
                    _opponent_choices(client, team, virtual, opponent)
                ]
            elif action not in {"prepare_battle_start", "battle_start"}:
                payload["off_team_btl"] = _mode_payload(
                    client, team, virtual, opponent
                )
            if action in {"top", "set_defence_deck", "set_offence_deck",
                          "battle_win"}:
                payload.setdefault("user", _battle_user_payload(client, state))
            if action == "top":
                payload.update({
                    "daily_time": "2037-12-31 23:59:59",
                    "monthly_time": "2037-12-31 23:59:59",
                    "other_rank": 0,
                })
            _save_state(client, state)
            _save_ladder(ladder)
            payload["response"] = ok
            return payload
