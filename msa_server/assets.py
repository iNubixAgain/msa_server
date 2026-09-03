"""Entrega de assets, tablas maestras y respuestas estáticas del cliente."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import FILES, STATIC_ROUTES
from .inventory import owned_units_payload
from .storage import load_client_json, response_file


@dataclass(frozen=True)
class AssetResponse:
    body: bytes
    status: int = 200
    content_type: str = "application/json; charset=utf-8"


class AssetController:
    def handle(self, path: str, params: dict[str, list[str]],
               client: str, ok: dict) -> AssetResponse | None:
        if path in {"/user/get_units", "/user/set_units"}:
            return AssetResponse(json.dumps(
                owned_units_payload(client), separators=(",", ":")
            ).encode("utf-8"))
        if path == "/build_up/get_deck":
            return AssetResponse(json.dumps(
                load_client_json(client, "user_deck.json", []),
                separators=(",", ":"),
            ).encode("utf-8"))
        prefix = "/snkp/msatk/prod/pack/0/"
        if path == prefix + "pack_info_list.txt":
            return AssetResponse(b'{"pack":[],"file":[]}',
                                 content_type="application/json")
        if path.startswith(prefix):
            name = Path(path[len(prefix):]).name
            target = FILES / name
            if target.is_file():
                return AssetResponse(target.read_bytes(),
                                     content_type="application/octet-stream")

        legacy_prefix = "/snkp/msatk/prod/7130000/"
        if path.startswith(legacy_prefix):
            name = Path(path[len(legacy_prefix):]).name
            target = FILES / name
            if target.is_file():
                return AssetResponse(target.read_bytes(),
                                     content_type="application/octet-stream")

        if path == "/title/get_master_table":
            tables = params.get("table[]", params.get("table", []))
            encoded_tables = []
            for requested in tables:
                name = requested.rsplit("/", 1)[-1]
                digest = hashlib.md5(name.encode("utf-8")).hexdigest()
                target = FILES / digest
                if not target.is_file():
                    logging.error("Falta tabla maestra %s (%s)", name, digest)
                    return AssetResponse(
                        b'{"error":"master_table_not_found"}', status=404
                    )
                encoded_tables.append({
                    name: base64.b64encode(target.read_bytes()).decode("ascii")
                })
            return AssetResponse(json.dumps(
                {"table": encoded_tables, "response": ok},
                separators=(",", ":"),
            ).encode())

        name = STATIC_ROUTES.get(path)
        if name:
            if name == "user_units.json":
                return AssetResponse(json.dumps(
                    owned_units_payload(client), separators=(",", ":")
                ).encode("utf-8"))
            if name == "user_deck.json":
                return AssetResponse(json.dumps(
                    load_client_json(client, name, []), separators=(",", ":")
                ).encode("utf-8"))
            target = response_file(name)
            if target:
                return AssetResponse(target.read_bytes())
        return None
