"""Lectura, normalización y persistencia de perfiles de jugador."""

from __future__ import annotations

import json

from .storage import atomic_write, client_data_file, client_lock, load_client_json


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalise_profile(profile, default_name: str = "user") -> dict:
    """Devuelve el subconjunto estable que comparten perfiles y rankings."""
    if not isinstance(profile, dict):
        profile = {}
    return {
        "name": str(profile.get("name", default_name)),
        "avatar": _int(profile.get("avatar"), 1),
        "language": _int(profile.get("language"), 8),
        "profile": str(profile.get("profile", "MSA LAN")),
        "play_type": _int(profile.get("play_type"), 0),
        "is_accept": _int(profile.get("is_accept"), 0),
        "base_avatar": _int(profile.get("base_avatar"), 1),
    }


def get_profile(client: str, default_name: str = "user") -> dict:
    return normalise_profile(
        load_client_json(client, "profile.json", {}), default_name
    )


def profile_detail(profile: dict, guild_name: str = "") -> dict:
    return {
        "guild_name": guild_name, "language": profile["language"],
        "profile": profile["profile"], "friend_id": 0,
        "play_type": profile["play_type"], "is_accept": profile["is_accept"],
        "friend_type": 0, "last_login_time": "2023-01-12 15:30:00",
        "arena_honor": 0,
    }


def update_profile(params: dict[str, list[str]], client: str,
                   default_name: str) -> dict:
    """Aplica solamente los campos enviados y conserva el resto del perfil."""
    with client_lock(client):
        profile = get_profile(client, default_name)
        if "name" in params:
            profile["name"] = params["name"][0]
        if "profile" in params:
            profile["profile"] = params["profile"][0]
        for request_key, stored_key in {
            "avatar": "avatar", "language": "language",
            "play_type": "play_type", "is_accept": "is_accept",
            "base_avatar": "base_avatar",
        }.items():
            if request_key in params:
                try:
                    profile[stored_key] = int(params[request_key][0])
                except (TypeError, ValueError):
                    pass
        atomic_write(
            client_data_file(client, "profile.json"),
            json.dumps(profile, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8"),
        )
        return profile


# Alias temporales para que la migración desde server.py sea gradual y segura.
client_profile = get_profile
persist_profile = update_profile
