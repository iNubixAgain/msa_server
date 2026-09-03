"""Seguimiento de sesiones, batallas PvP y registro de usuarios."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import NICKNAME_LOG_FILE, SESSION_TIMEOUT, USER_LOG_FILE
from .identity import public_player_id
from .profiles import get_profile


_INTERNAL_CLIENT_RE = re.compile(r"(?<=Cliente: )(client-[0-9a-f]{24})")


def _display_client(client: str) -> str:
    """Devuelve el mismo identificador público que ve el resto del servidor."""
    clean = str(client).replace("\n", " ").replace("\r", " ").strip()
    if clean.startswith("client-"):
        return public_player_id(clean)
    return clean


@dataclass
class UserSession:
    client: str
    ip: str
    nickname: str
    last_seen: float
    connected: bool = True


@dataclass
class PvPPlayerInfo:
    client: str
    nickname: str
    ip: str


@dataclass
class PvPBattle:
    room_id: str
    mode: str
    players: list[PvPPlayerInfo]
    started_at: float
    started_str: str
    finished_clients: set[str] = field(default_factory=set)


class UserTracker:
    """Rastrea conexiones activas, batallas PvP y escribe logs de actividad."""

    def __init__(self, log_path: Path = USER_LOG_FILE,
                 nickname_log_path: Path = NICKNAME_LOG_FILE,
                 timeout: float = SESSION_TIMEOUT) -> None:
        self.log_path = Path(log_path)
        self.nickname_log_path = Path(nickname_log_path)
        self.timeout = float(timeout)
        self._lock = threading.RLock()
        self._sessions: dict[str, UserSession] = {}
        self._pvp_battles: dict[str, PvPBattle] = {}
        self._entries: list[str] = []
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._load_existing_log()

    def _load_existing_log(self) -> None:
        if not self.log_path.is_file():
            return
        try:
            raw_text = self.log_path.read_text(encoding="utf-8")
            for line in raw_text.splitlines():
                stripped = line.strip()
                if " | IP: " in stripped and " | Nickname: " in stripped and "CAMBIO DE NICKNAME" not in stripped:
                    # Los logs antiguos mostraban la clave interna y parecían
                    # pertenecer a otro usuario. Normalizarlos al cargarlos evita
                    # mezclar ambos espacios de identificadores en UserLog.log.
                    self._entries.append(_INTERNAL_CLIENT_RE.sub(
                        lambda match: public_player_id(match.group(1)), stripped
                    ))
        except OSError:
            pass

    def start(self) -> None:
        with self._lock:
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                name="user-session-monitor",
                daemon=True,
            )
            self._monitor_thread.start()
            self._rewrite_log()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for session in self._sessions.values():
                if session.connected:
                    session.connected = False
                    self._add_entry(now_str, session.ip, session.nickname,
                                    "DESCONEXION (Cierre del servidor)",
                                    client=session.client)
            self._pvp_battles.clear()
            self._rewrite_log()
            thread = self._monitor_thread
        if thread is not None and thread.is_alive() and threading.current_thread() != thread:
            thread.join(timeout=2.0)

    def record_activity(self, client: str, ip: str,
                        nickname: str | None = None) -> None:
        if not client:
            return
        now_mono = time.monotonic()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            session = self._sessions.get(client)
            current_nick = nickname
            if current_nick is None:
                profile = get_profile(client, f"PLAYER {ip}")
                current_nick = str(profile.get("name") or f"PLAYER {ip}")

            if session is None or not session.connected:
                self._sessions[client] = UserSession(
                    client=client,
                    ip=ip,
                    nickname=current_nick,
                    last_seen=now_mono,
                    connected=True,
                )
                self._add_entry(now_str, ip, current_nick, "CONEXION",
                                client=client)
                self._rewrite_log()
            else:
                session.last_seen = now_mono
                session.ip = ip
                if nickname and session.nickname != nickname:
                    session.nickname = nickname
                    self._rewrite_log()

    def update_nickname(self, client: str, new_nickname: str) -> None:
        if not client or not new_nickname:
            return
        clean_nick = str(new_nickname).strip()
        with self._lock:
            session = self._sessions.get(client)
            if session:
                session.nickname = clean_nick
            for battle in self._pvp_battles.values():
                for p in battle.players:
                    if p.client == client:
                        p.nickname = clean_nick
            self._rewrite_log()

    def record_nickname_change(self, client: str, ip: str,
                               old_nickname: str, new_nickname: str) -> None:
        clean_old = str(old_nickname).replace("\n", " ").replace("\r", " ").strip()
        clean_new = str(new_nickname).replace("\n", " ").replace("\r", " ").strip()
        clean_ip = str(ip).replace("\n", " ").replace("\r", " ").strip()
        clean_client = _display_client(client)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            session = self._sessions.get(client)
            if session:
                session.nickname = clean_new
            for battle in self._pvp_battles.values():
                for p in battle.players:
                    if p.client == client:
                        p.nickname = clean_new

            # Registrar en Nicknames.log
            nick_entry = (
                f"{now_str} | Cliente: {clean_client} | IP: {clean_ip} | Nickname Anterior: {clean_old} | "
                f"Nickname Nuevo: {clean_new}\n"
            )
            try:
                self.nickname_log_path.parent.mkdir(parents=True, exist_ok=True)
                write_header = (
                    not self.nickname_log_path.exists()
                    or self.nickname_log_path.stat().st_size == 0
                )
                with open(self.nickname_log_path, "a", encoding="utf-8") as handle:
                    if write_header:
                        handle.write("# Registro de Cambios de Nickname\n\n")
                    handle.write(nick_entry)
                    handle.flush()
            except OSError:
                logging.exception(
                    "No se pudo escribir en Nicknames.log: %s",
                    self.nickname_log_path,
                )

            self._rewrite_log()

    def record_pvp_battle_start(self, mode: str, room_id: str,
                                clients: list[str], source_ip: str = "") -> None:
        if not room_id or not clients:
            return
        now_mono = time.monotonic()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            if room_id in self._pvp_battles:
                return
            players: list[PvPPlayerInfo] = []
            for client_id in clients:
                session = self._sessions.get(client_id)
                if session is not None:
                    nick = session.nickname
                    ip_addr = session.ip
                else:
                    prof = get_profile(client_id, f"PLAYER {client_id}")
                    nick = str(prof.get("name") or f"PLAYER {client_id}")
                    ip_addr = source_ip or "127.0.0.1"
                players.append(PvPPlayerInfo(client=client_id, nickname=nick, ip=ip_addr))

            battle = PvPBattle(
                room_id=room_id,
                mode=mode,
                players=players,
                started_at=now_mono,
                started_str=now_str,
                finished_clients=set(),
            )
            self._pvp_battles[room_id] = battle
            for player in players:
                self._add_entry(now_str, player.ip, player.nickname,
                                f"BATALLA PVP {mode.upper()} (Inicio - Sala {room_id})",
                                client=player.client)
            self._rewrite_log()

    def record_pvp_battle_end(self, mode: str, room_id: str, client: str) -> None:
        if not room_id:
            return
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            battle = self._pvp_battles.get(room_id)
            if not battle:
                return
            if client in battle.finished_clients:
                return
            battle.finished_clients.add(client)
            session = self._sessions.get(client)
            nick = session.nickname if session else get_profile(client).get("name", client)
            ip_addr = session.ip if session else "127.0.0.1"
            self._add_entry(now_str, ip_addr, nick,
                            f"BATALLA PVP {mode.upper()} (Fin - Sala {room_id})",
                            client=client)

            all_clients = {p.client for p in battle.players}
            if all_clients.issubset(battle.finished_clients):
                self._pvp_battles.pop(room_id, None)
            self._rewrite_log()

    def check_timeouts(self) -> list[UserSession]:
        now_mono = time.monotonic()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        timed_out: list[UserSession] = []
        changed = False
        with self._lock:
            for session in self._sessions.values():
                if session.connected and (now_mono - session.last_seen) > self.timeout:
                    session.connected = False
                    self._add_entry(now_str, session.ip, session.nickname,
                                    "DESCONEXION", client=session.client)
                    timed_out.append(session)
                    changed = True

            # Limpiar batallas huérfanas / inactivas de más de 10 minutos
            stale_battles = [
                room_id for room_id, battle in self._pvp_battles.items()
                if (now_mono - battle.started_at) > 600.0
            ]
            for room_id in stale_battles:
                self._pvp_battles.pop(room_id, None)
                changed = True

            if changed:
                self._rewrite_log()
        return timed_out

    def get_active_sessions(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "client": s.client,
                    "ip": s.ip,
                    "nickname": s.nickname,
                    "last_seen": s.last_seen,
                    "connected": s.connected,
                }
                for s in self._sessions.values()
                if s.connected
            ]

    def get_active_battles(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "room_id": b.room_id,
                    "mode": b.mode,
                    "started_str": b.started_str,
                    "players": [
                        {"client": p.client, "nickname": p.nickname, "ip": p.ip,
                         "finished": p.client in b.finished_clients}
                        for p in b.players
                    ],
                }
                for b in self._pvp_battles.values()
            ]

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(5.0):
            try:
                self.check_timeouts()
            except Exception:
                logging.exception("Error en el monitor de sesiones de usuario")

    def _add_entry(self, timestamp: str, ip: str, nickname: str, status: str,
                   client: str = "") -> None:
        clean_ip = str(ip).replace("\n", " ").replace("\r", " ").strip()
        clean_nick = str(nickname).replace("\n", " ").replace("\r", " ").strip()
        clean_client = _display_client(client)
        client_part = f" | Cliente: {clean_client}" if clean_client else ""
        line = f"{timestamp} | IP: {clean_ip}{client_part} | Nickname: {clean_nick} | {status}"
        self._entries.append(line)

    def _render_content(self) -> str:
        lines = ["# Registro de Usuarios de Metal Slug Attack", ""]
        lines.append("## Batallas PvP Activas")
        lines.append("")
        if not self._pvp_battles:
            lines.append("- Ninguna batalla en curso.")
        else:
            for battle in self._pvp_battles.values():
                lines.append(
                    f"- **[{battle.mode.upper()}] Sala `{battle.room_id}`** (Iniciada: {battle.started_str}):"
                )
                for p in battle.players:
                    status = " (Finalizado)" if p.client in battle.finished_clients else " (En combate)"
                    lines.append(
                        f"  - Nickname: {p.nickname} "
                        f"(Cliente: {_display_client(p.client)}, IP: {p.ip}){status}"
                    )
        lines.append("")
        lines.append("## Historial de Conexiones y Eventos")
        lines.append("")
        if not self._entries:
            lines.append("- Sin registros aún.")
        else:
            for entry in self._entries:
                lines.append(entry)
        lines.append("")
        return "\n".join(lines)

    def _rewrite_log(self) -> None:
        content = self._render_content()
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
        except OSError:
            logging.exception("No se pudo escribir en el log de usuarios: %s",
                              self.log_path)


USER_TRACKER = UserTracker()
