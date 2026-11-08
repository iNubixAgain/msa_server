"""Construcción de contratos de usuario compartidos por las secciones."""

from __future__ import annotations

from .identity import public_player_id
from .inventory import owned_units_payload
from .decks import load_decks
from .profiles import get_profile
from .storage import load_json


def user_payload(identifier: str = "msa-offline", name: str = "user",
                 client: str | None = None) -> dict:
    if client:
        # Debe coincidir con ``person.user`` en Social y Guild. El cliente usa
        # esta igualdad para localizar al jugador actual dentro de las listas.
        identifier = public_player_id(client)
    profile = get_profile(client, name) if client else {
        "name": name, "avatar": 1, "language": 0, "base_avatar": 1,
    }
    online_score = 0
    if client:
        try:
            from .online import get_online_stats
            online_score = get_online_stats(client).get("online_score", 0)
        except Exception:
            online_score = 0
    return {
        "open_id": identifier, "name": profile["name"], "level": 50,
        "avatar_id": profile["avatar"], "online_score": online_score,
        "arena_honor": 0, "language": profile["language"],
        "pp": 450000, "medal": 9999999, "msp": 99999999,
        "sneak_coin": 999999, "battle_coin": 999999, "guild_coin": 999999,
        "mars_coin": 999999, "support_exp": 999999, "stamina": 99,
        "max_stamina": 99, "recovery_time": "2023-01-12 15:30:00",
        "units": owned_units_payload(client),
        "deck": (load_decks(client) if client else
                 load_json("user_deck.json", [])), "increase_deck": 6,
        "base_avatar": profile["base_avatar"],
        "items": [{"item_id": item_id, "quantity": 999999}
                  for item_id in range(1, 5324)],
        "build_up": [50] * 11,
        "customize_affiliation": [
            {"type": 1, "level": [50] * 6},
            {"type": 2, "level": [50] * 6},
            {"type": 3, "level": [50] * 6},
        ],
        "vip": 0, "vip_point": 0,
        # Este campo es GuildKind (0 ninguno, 1 normal, 2 one-time), no el ID.
        "guild": (_guild_kind(client) if client else 0),
    }


def _guild_kind(client: str) -> int:
    # Import diferido para mantener users.py como contrato compartido y evitar
    # un ciclo al construir las vistas detalladas de Guild.
    from .guild import guild_kind_for_client
    return guild_kind_for_client(client)


def ok_payload(ok: dict) -> dict:
    return {"response": ok}


def user_only(ok: dict, client: str | None = None) -> dict:
    return {"user": user_payload(client=client), "response": ok}
