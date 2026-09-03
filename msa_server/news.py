"""Controlador y gestión de noticias y mensajes del servidor a clientes (Event News)."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Mapping

from .storage import atomic_write, DATA

_lock = threading.RLock()

DEFAULT_NEWS = [
    {
        "id": 1,
        "kind": 1,
        "subject": "¡Bienvenido a Metal Slug Attack Online!",
        "body": "Servidor activo con Battle Tactics 24/7, Online 1v1 y 2v2, y eventos Extra Ops.",
        "img": "",
        "unread": 1,
    }
]


def news_file_path() -> Path:
    return DATA / "news.json"


def load_news() -> list[dict]:
    path = news_file_path()
    with _lock:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception as e:
                logging.warning(f"Error al leer news.json: {e}")
        # Inicializa archivo si no existe
        save_news(DEFAULT_NEWS)
        return list(DEFAULT_NEWS)


def save_news(news: list[dict]) -> None:
    path = news_file_path()
    with _lock:
        atomic_write(path, json.dumps(news, indent=2, ensure_ascii=False).encode("utf-8"))


def add_news_message(subject: str, body: str, kind: int = 1, img: str = "",
                     unread: int = 1) -> dict:
    with _lock:
        current = load_news()
        next_id = max([item.get("id", 0) for item in current], default=0) + 1
        msg = {
            "id": next_id,
            "kind": kind,
            "subject": subject,
            "body": body,
            "img": img,
            "unread": unread,
        }
        current.insert(0, msg)
        save_news(current)
        return msg


class NewsController:
    """Atiende el endpoint /event_info/get_news para enviar avisos y mensajes del servidor."""

    prefix = "/event_info/"

    def handles(self, path: str) -> bool:
        return path == "/event_info/get_news" or path.startswith(self.prefix)

    def handle(self, path: str, client: str, params: Mapping[str, list[str]],
               ok: dict) -> dict | None:
        if not self.handles(path):
            return None

        action = path.rstrip("/").rsplit("/", 1)[-1]
        if action == "get_news":
            return {
                "news": load_news(),
                "response": ok,
            }

        return None
