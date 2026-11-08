"""Payloads base de contenido individual reutilizados por el router."""

from __future__ import annotations

from .storage import load_json


def world_map_payload() -> dict:
    return load_json("world_map.json", {"stage": []})


def story_payload() -> dict:
    return load_json("story.json", {"story_map": []})


def rental_soldier() -> dict:
    return {"unit": [], "available": 0}
