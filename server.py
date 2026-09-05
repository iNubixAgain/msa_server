#!/usr/bin/env python3
"""Servidor LAN persistente para el mod offline de Metal Slug Attack."""

from __future__ import annotations

import json
import hashlib
import logging
import threading
import os
import ssl
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from msa_server.assets import AssetController
from msa_server import accounts
from msa_server.bootstrap import prepare_client
from msa_server.clock import response_clock
from msa_server.config import (
    CAPTURE_HTTP_DIAGNOSTICS,
    DATA,
    DIAGNOSTIC_QUEUE_SIZE,
    FILES,
    HOST,
    LOG_FILE,
    MAX_REQUEST_BODY,
    SERVER_CONTENT,
    ONLINE_RUNTIME,
    PHOTON_SERVER,
    PORT,
    PUBLIC_HOST,
    PUBLIC_PORT,
    SAVE_ROUTES,
    USER_LOG_FILE,
    NICKNAME_LOG_FILE,
)
from msa_server.diagnostics import DiagnosticWriter
from msa_server.battle_tactics import BattleTacticsController
from msa_server.battle import BattleController, TeamBattleController
from msa_server.arena import ArenaController
from msa_server.core import CoreController
from msa_server.gameplay import GameplayController
from msa_server.friend_online import FriendOnlineController
from msa_server.gacha import GachaController
from msa_server.guild import GuildController
from msa_server.guild_online import GuildOnlineController
from msa_server.identity import resolve_client_identity
from msa_server.lifecycle import acquire_single_instance_lock, configure_logging
from msa_server.matchmaking import OnlineMatchmaker
from msa_server.news import NewsController
from msa_server.notifications import InGameNotificationController
from msa_server.online import OnlineController
from msa_server.photon import PhotonSupervisor
from msa_server.sessions import USER_TRACKER
from msa_server.social import SocialController
from msa_server.snatch_wars import SnatchWarsController
from msa_server.ultimate_mission import UltimateMissionController
from msa_server.storage import (
    atomic_write,
    client_data_file,
)

_lock = threading.RLock()


NOTIFICATIONS = InGameNotificationController()
SOCIAL = SocialController()
MATCHMAKER = OnlineMatchmaker()
ONLINE = OnlineController(MATCHMAKER, USER_TRACKER)
ARENA = ArenaController(MATCHMAKER, USER_TRACKER)
FRIEND_ONLINE = FriendOnlineController(MATCHMAKER, SOCIAL, USER_TRACKER)
GUILD_ONLINE = GuildOnlineController(MATCHMAKER, SOCIAL, USER_TRACKER)
BATTLE_TACTICS = BattleTacticsController(MATCHMAKER)
BATTLE = BattleController()
TEAM_BATTLE = TeamBattleController()
NEWS = NewsController()
GAMEPLAY = GameplayController()
GACHA = GachaController()
ULTIMATE_MISSION = UltimateMissionController()
GUILD = GuildController()
SNATCH_WARS = SnatchWarsController()
CORE = CoreController(SOCIAL, USER_TRACKER, NOTIFICATIONS)
ASSETS_CONTROLLER = AssetController()


class RequestBodyTooLarge(Exception):
    pass


class RobustThreadingHTTPServer(ThreadingHTTPServer):
    """Servidor concurrente sin límite artificial de solicitudes."""

    daemon_threads = True
    block_on_close = False
    request_queue_size = 256

    def __init__(self, *args, **kwargs):
        self._worker_count_lock = threading.Lock()
        self.active_requests = 0
        self.photon_supervisor: PhotonSupervisor | None = None
        self.diagnostic_writer: DiagnosticWriter | None = None
        super().__init__(*args, **kwargs)
        cert = os.environ.get("MSA_TLS_CERT", "")
        key = os.environ.get("MSA_TLS_KEY", "")
        if bool(cert) != bool(key):
            self.server_close()
            raise ValueError("Set both MSA_TLS_CERT and MSA_TLS_KEY")
        if cert:
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(cert, key)
                self.socket = context.wrap_socket(self.socket, server_side=True)
            except Exception:
                self.server_close()
                raise

    def process_request_thread(self, request, client_address) -> None:
        with self._worker_count_lock:
            self.active_requests += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._worker_count_lock:
                self.active_requests -= 1


class Handler(BaseHTTPRequestHandler):
    # No publicar el producto, la versión de Python ni la arquitectura usada.
    server_version = "HTTP"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(30)
        self._response_started = False

    def log_message(self, fmt: str, *args: object) -> None:
        # SOLICITUD/RESPUESTA ya contienen la información útil. Evita una
        # tercera entrada por petición salvo que se active nivel DEBUG.
        logging.debug("HTTP request from %s", self.client_address[0])

    def send_bytes(self, body: bytes, status: int = 200,
                   content_type: str = "application/json; charset=utf-8") -> None:
        if status >= 400:
            body = b'{"response":{"error_code":1}}'
            content_type = "application/json; charset=utf-8"
        logging.info("RESPUESTA %s %s bytes=%d tipo=%s", status, urlsplit(self.path).path,
                     len(body), content_type)
        self._response_started = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        device_cookie = getattr(self, "_new_device_cookie", "")
        if device_cookie:
            self.send_header(
                "Set-Cookie",
                f"msa_device={device_cookie}; Path=/; Max-Age=315360000; SameSite=Lax",
            )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_body(self) -> bytes:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if size < 0:
            size = 0
        if size > MAX_REQUEST_BODY:
            self.close_connection = True
            raise RequestBodyTooLarge(size)
        return self.rfile.read(size) if size else b""

    def dispatch(self, persist: bool = False) -> None:
        try:
            self.route(persist=persist)
        except accounts.AccountError as exc:
            logging.warning(
                "OPERACION_CUENTA rechazada ruta=%s codigo=%s desde=%s",
                urlsplit(self.path).path, exc.code, self.client_address[0],
            )
            payload = {"response": {**response_clock(), "error_code": exc.code}}
            self.send_bytes(json.dumps(payload).encode())
        except RequestBodyTooLarge as exc:
            logging.warning("Petición rechazada por tamaño: ip=%s bytes=%s",
                            self.client_address[0], exc.args[0])
            if not self._response_started:
                self.send_bytes(b"", status=413)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            logging.info("Cliente cerró la conexión durante %s desde=%s",
                         urlsplit(self.path).path, self.client_address[0])
        except Exception:
            logging.exception("Fallo aislado atendiendo %s desde=%s",
                              urlsplit(self.path).path, self.client_address[0])
            self.close_connection = True
            if not self._response_started:
                try:
                    self.send_bytes(b"", status=500)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass

    def client_identity(self) -> str:
        """Identidad estable por instalación, sin depender solamente de la IP."""
        resolution = resolve_client_identity(
            self.headers, self.client_address[0]
        )
        if resolution.new_cookie:
            self._new_device_cookie = resolution.new_cookie
        return resolution.client

    def route(self, persist: bool = False) -> None:
        path = unquote(urlsplit(self.path).path).rstrip("/") or "/"
        if path == "/title/transfer":
            if not persist or urlsplit(self.path).query:
                raise accounts.AccountError(400)
            body = self.read_body()
            if "application/x-www-form-urlencoded" not in self.headers.get("Content-Type", ""):
                raise accounts.AccountError(400)
            params = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            payload = accounts.transfer(self.headers, params,
                                        self.client_address[0], MATCHMAKER.account_busy)
            self._new_device_cookie = payload["terminal_id"]
            payload["response"] = response_clock()
            return self.send_bytes(json.dumps(payload).encode())
        client = self.client_identity()
        with accounts.request_guard(self.headers, client):
            self.route_account(client, persist)

    def route_account(self, client: str, persist: bool = False) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        body = self.read_body() if persist else b""
        params = parse_qs(parsed.query, keep_blank_values=True)
        content_type = self.headers.get("Content-Type", "")
        if path not in {"/__offline_status", "/__health"}:
            prepare_client(client, self.client_address[0])
        logging.info("SOLICITUD %s %s desde=%s bytes=%d", self.command, path,
                     self.client_address[0], len(body))
        if body and "application/x-www-form-urlencoded" in content_type:
            posted = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            for key, values in posted.items():
                params.setdefault(key, []).extend(values)

        if path not in {"/__offline_status", "/__health"}:
            initial_nick = params.get("name", [None])[0] if path == "/setting/change_setting" else None
            USER_TRACKER.record_activity(client, self.client_address[0], nickname=initial_nick)

        if path in accounts.SENSITIVE_ROUTES:
            if not persist or parsed.query:
                raise accounts.AccountError(400)
            if path == "/setting/upload_save_data":
                if not accounts.credential(self.headers):
                    raise accounts.AccountError()
                accounts.save_backup(client, params)
                payload = {}
            else:
                payload = accounts.issue(self.headers, client, params)
            payload["response"] = response_clock()
            return self.send_bytes(json.dumps(payload).encode())

        if persist and CAPTURE_HTTP_DIAGNOSTICS:
            safe_name = path.strip("/").replace("/", "__") or "root"
            sensitive = any(key in params for key in (
                "password", "secret_key", "terminal_id", "save_data"))
            record = {"path": path, "query": "[REDACTED]" if sensitive else parsed.query,
                      "saved_at": datetime.now(timezone.utc).isoformat(),
                      "source_ip": self.client_address[0],
                      "client_identity": client,
                      "terminal_id_hash": hashlib.sha256(
                          self.headers.get("terminal_id", "").encode(
                              "utf-8", errors="replace"
                          )
                      ).hexdigest()[:16],
                      "content_type": content_type,
                      "user_agent": self.headers.get("User-Agent", ""),
                      "header_names": sorted(self.headers.keys(), key=str.lower),
                      "body": "[REDACTED]" if sensitive else body.decode("utf-8", errors="replace")}
            encoded_record = json.dumps(
                record, ensure_ascii=False, indent=2
            ).encode("utf-8")
            global_path = DATA / "requests" / f"{safe_name}.json"
            client_path = client_data_file(client, f"requests/{safe_name}.json")
            writer = getattr(self.server, "diagnostic_writer", None)
            if writer is not None:
                writer.submit(global_path, client_path, encoded_record)
            else:
                # Mantiene el comportamiento determinista en usos embebidos y
                # pruebas que no arrancan el ciclo de vida completo.
                with _lock:
                    atomic_write(global_path, encoded_record)
                    atomic_write(client_path, encoded_record)

        ok = response_clock()

        if path in {"/__offline_status", "/__health"}:
            if self.client_address[0] not in {"127.0.0.1", "::1"}:
                logging.warning("Diagnóstico remoto rechazado desde=%s ruta=%s",
                                self.client_address[0], path)
                return self.send_bytes(b"", status=404)
            supervisor = getattr(self.server, "photon_supervisor", None)
            photon_alive = bool(supervisor and supervisor.is_alive())
            photon_metrics = supervisor.snapshot() if supervisor else {}
            writer = getattr(self.server, "diagnostic_writer", None)
            diagnostic_metrics = writer.snapshot() if writer else {}
            payload = {"server": "MSA-LAN", "version": 2,
                       "master_tables": len(list(FILES.iterdir())),
                       "persistence": True, "photon_alive": photon_alive,
                       "active_http_requests": getattr(
                           self.server, "active_requests", 1
                       ), "http_worker_limit": None,
                       "photon": photon_metrics,
                       "diagnostic_queue": diagnostic_metrics,
                       "http_diagnostic_capture": CAPTURE_HTTP_DIAGNOSTICS,
                       "response": ok}
            status = 200 if path == "/__offline_status" or photon_alive else 503
            return self.send_bytes(json.dumps(
                payload, separators=(",", ":")
            ).encode(), status=status)

        if ONLINE.handles(path):
            payload = ONLINE.handle(
                path, client, params, self.client_address[0], ok
            )
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        if BATTLE.handles(path):
            payload = BATTLE.handle(path, client, params, ok)
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        if TEAM_BATTLE.handles(path):
            payload = TEAM_BATTLE.handle(path, client, params, ok)
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        if ARENA.handles(path):
            payload = ARENA.handle(
                path, client, params, self.client_address[0], ok
            )
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        if FRIEND_ONLINE.handles(path):
            payload = FRIEND_ONLINE.handle(
                path, client, params, self.client_address[0], ok
            )
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        if GUILD_ONLINE.handles(path):
            payload = GUILD_ONLINE.handle(
                path, client, params, self.client_address[0], ok
            )
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        if NOTIFICATIONS.handles(path):
            payload = NOTIFICATIONS.handle(path, client, params, ok)
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        payload = CORE.handle(
            path, client, params, self.client_address[0], self.headers, ok,
            registration_token=getattr(self, "_new_device_cookie", ""),
        )
        if payload is not None:
            return self.send_bytes(json.dumps(
                payload, separators=(",", ":")
            ).encode())

        if SOCIAL.handles(path):
            payload = SOCIAL.handle(path, client, params, ok)
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        if GUILD.handles(path):
            payload = GUILD.handle(path, client, params, ok)
            if payload is not None:
                if path == "/guild_battle/top":
                    payload["last_message_date"] = SOCIAL.menu_status(client)["last_message_date"]
                events = payload.pop("_server_events", [])
                for event in events if isinstance(events, list) else []:
                    if (isinstance(event, dict)
                            and event.get("type") == "guild_join"):
                        SOCIAL.record_guild_join(
                            str(event.get("client", "")),
                            int(event.get("guild_id", 0)),
                        )
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        payload = GACHA.handle(path, client, params, ok)
        if payload is not None:
            return self.send_bytes(json.dumps(
                payload, separators=(",", ":")
            ).encode())

        payload = ULTIMATE_MISSION.handle(path, client, params, ok)
        if payload is not None:
            return self.send_bytes(json.dumps(
                payload, separators=(",", ":")
            ).encode())

        payload = SNATCH_WARS.handle(path, client, params, ok)
        if payload is not None:
            return self.send_bytes(json.dumps(
                payload, separators=(",", ":")
            ).encode())

        if BATTLE_TACTICS.handles(path):
            payload = BATTLE_TACTICS.handle(
                path, client, params, self.client_address[0], ok
            )
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        if NEWS.handles(path):
            payload = NEWS.handle(path, client, params, ok)
            if payload is not None:
                return self.send_bytes(json.dumps(
                    payload, separators=(",", ":")
                ).encode())

        payload = GAMEPLAY.handle(path, client, params, ok)
        if payload is not None:
            return self.send_bytes(json.dumps(
                payload, separators=(",", ":")
            ).encode())

        asset_response = ASSETS_CONTROLLER.handle(path, params, client, ok)
        if asset_response is not None:
            return self.send_bytes(
                asset_response.body, status=asset_response.status,
                content_type=asset_response.content_type,
            )

        if persist:
            safe_name = path.strip("/").replace("/", "__") or "root"
            with _lock:
                save_name = SAVE_ROUTES.get(path)
                if save_name and body:
                    try:
                        json.loads(body)
                        atomic_write(DATA / save_name, body)
                    except json.JSONDecodeError:
                        pass
            # Las escrituras del API esperan una respuesta JSON exitosa.
            return self.send_bytes(b'{"result":0,"status":0}')

        logging.warning("Ruta no publicada: %s %s desde=%s", self.command,
                        path, self.client_address[0])
        self.send_bytes(b"", status=404)

    def do_GET(self) -> None:
        self.dispatch()

    def do_HEAD(self) -> None:
        self.dispatch()

    def do_POST(self) -> None:
        self.dispatch(persist=True)


def main() -> None:
    instance_lock = acquire_single_instance_lock()
    log_listener = configure_logging()
    if not SERVER_CONTENT.is_dir():
        instance_lock.close()
        log_listener.stop()
        raise SystemExit(f"No existe la carpeta de contenido: {SERVER_CONTENT}")
    if not PHOTON_SERVER.is_file():
        instance_lock.close()
        log_listener.stop()
        raise SystemExit(f"No existe el servidor online: {PHOTON_SERVER}")
    photon = PhotonSupervisor(PHOTON_SERVER, ONLINE_RUNTIME)
    diagnostic_writer = DiagnosticWriter(DIAGNOSTIC_QUEUE_SIZE)
    server: RobustThreadingHTTPServer | None = None
    try:
        diagnostic_writer.start()
        photon.start()
        USER_TRACKER.start()
        server = RobustThreadingHTTPServer((HOST, PORT), Handler)
        server.photon_supervisor = photon
        server.diagnostic_writer = diagnostic_writer
        scheme = "https" if os.environ.get("MSA_TLS_CERT") else "http"
        logging.info("MSA escuchando localmente en %s://%s:%s", scheme, HOST, PORT)
        logging.info("Endpoint HTTP publico: %s://%s:%s",
                     scheme, PUBLIC_HOST, PUBLIC_PORT)
        logging.info("Photon activo en UDP 5055, 5056 y 5058")
        logging.info("Log automático: %s", LOG_FILE)
        logging.info("Log de usuarios: %s", USER_LOG_FILE)
        logging.info("Log de nicknames: %s", NICKNAME_LOG_FILE)
        print(f"MSA escuchando localmente en {scheme}://{HOST}:{PORT}")
        print(f"Endpoint HTTP publico: {scheme}://{PUBLIC_HOST}:{PUBLIC_PORT}")
        print("Photon activo en UDP 5055, 5056 y 5058")
        print(f"Log automático: {LOG_FILE}")
        print(f"Log de usuarios: {USER_LOG_FILE}")
        print(f"Log de nicknames: {NICKNAME_LOG_FILE}")
        print("Pulsa Ctrl+C para detenerlo.")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        USER_TRACKER.stop()
        if server is not None:
            server.server_close()
        photon.stop()
        diagnostic_writer.stop()
        instance_lock.close()
        logging.info("Servidor MSA detenido; cola de logs drenada")
        log_listener.stop()


if __name__ == "__main__":
    main()
