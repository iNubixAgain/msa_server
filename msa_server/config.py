"""Configuración y rutas compartidas por todos los módulos del backend."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SERVER_CONTENT = ROOT / "server_content"
FILES = SERVER_CONTENT / "files"
DATA = ROOT / "server_data"
SERVER_DEFAULTS = ROOT / "server_defaults"
HOST = os.environ.get("MSA_HOST", "0.0.0.0")
PORT = int(os.environ.get("MSA_PORT", "42069"))
PUBLIC_HOST = os.environ.get("MSA_PUBLIC_HOST", "online.nubix.playit.plus")
PUBLIC_PORT = int(os.environ.get("MSA_PUBLIC_PORT", "51516"))
# Revisión local de tablas 7.13.0. Invalida las tablas recortadas que recibieron
# las instalaciones nuevas antes de completar el registro.
MASTER_VERSION = int(os.environ.get("MSA_MASTER_VERSION", "7130017"))
ONLINE_RUNTIME = ROOT / "online_runtime"
PHOTON_SERVER = ONLINE_RUNTIME / "luxon_server.exe"
MAX_REQUEST_BODY = max(
    1024, int(os.environ.get("MSA_MAX_REQUEST_BODY", str(8 * 1024 * 1024)))
)
DIAGNOSTIC_QUEUE_SIZE = max(
    1, int(os.environ.get("MSA_DIAGNOSTIC_QUEUE_SIZE", "512"))
)
CAPTURE_HTTP_DIAGNOSTICS = os.environ.get(
    "MSA_CAPTURE_HTTP_DIAGNOSTICS", "0"
).strip().lower() in {"1", "true", "yes", "on"}
LOG_FILE = ROOT / "ServerLog.log"
USER_LOG_FILE = ROOT / "UserLog.log"
NICKNAME_LOG_FILE = ROOT / "Nicknames.log"
SESSION_TIMEOUT = float(os.environ.get("MSA_SESSION_TIMEOUT", "120.0"))

STATIC_ROUTES = {
    "/world_map/top": "world_map.json",
    "/story/top": "story.json",
    "/practice/1on1/start": "practice_1on1_start.json",
}

SAVE_ROUTES = {
}

DATA.mkdir(exist_ok=True)
