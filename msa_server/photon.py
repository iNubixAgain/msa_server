"""Supervisión del proceso Photon/Luxon usado por todas las salas online."""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


class PhotonSupervisor:
    """Mantiene Photon activo y evita bucles rápidos de reinicio."""

    _PACKET_TRACE_MARKERS = (
        "RaiseEvent actor=",
        "DispatchEvent code=",
    )

    def __init__(self, executable: Path, cwd: Path,
                 log_packet_events: bool | None = None) -> None:
        self.executable = executable
        self.cwd = cwd
        if log_packet_events is None:
            log_packet_events = os.environ.get(
                "MSA_PHOTON_PACKET_LOGS", ""
            ).strip().lower() in {"1", "true", "yes", "on"}
        self.log_packet_events = log_packet_events
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.output_threads: list[threading.Thread] = []
        self.log_queue: queue.SimpleQueue[str | None] = queue.SimpleQueue()
        self.log_thread: threading.Thread | None = None
        self.launched_at = 0.0
        self.launch_count = 0
        self.unexpected_exits = 0
        self.output_lines = 0
        self.suppressed_packet_lines = 0
        self.last_exit_code: int | None = None

    def _launch(self) -> None:
        process = subprocess.Popen(
            [str(self.executable)], cwd=self.cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        with self.lock:
            self.process = process
            self.launched_at = time.monotonic()
            self.launch_count += 1
        output_thread = threading.Thread(
            target=self._capture_output, args=(process,),
            name=f"photon-log-{process.pid}", daemon=True,
        )
        self.output_threads.append(output_thread)
        output_thread.start()
        logging.info("Photon iniciado pid=%s", process.pid)

    def _capture_output(self, process: subprocess.Popen) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            line = line.rstrip("\r\n")
            with self.lock:
                self.output_lines += 1
                suppress = (not self.log_packet_events
                            and any(marker in line
                                    for marker in self._PACKET_TRACE_MARKERS))
                if suppress:
                    self.suppressed_packet_lines += 1
            if not suppress:
                self.log_queue.put(line)

    def _write_output(self) -> None:
        while True:
            line = self.log_queue.get()
            if line is None:
                return
            logging.info("[Photon] %s", line)

    def start(self) -> None:
        self.log_thread = threading.Thread(
            target=self._write_output, name="photon-log-writer", daemon=True
        )
        self.log_thread.start()
        self._launch()
        self.thread = threading.Thread(
            target=self._monitor, name="photon-supervisor", daemon=True
        )
        self.thread.start()

    def _monitor(self) -> None:
        restart_delay = 1
        while not self.stop_event.wait(1):
            with self.lock:
                process = self.process
                launched_at = self.launched_at
            if process is None or process.poll() is None:
                continue
            uptime = time.monotonic() - launched_at
            with self.lock:
                self.unexpected_exits += 1
                self.last_exit_code = process.returncode
            logging.error("Photon terminó inesperadamente código=%s uptime=%.1fs",
                          process.returncode, uptime)
            restart_delay = 1 if uptime >= 30 else min(restart_delay * 2, 30)
            if self.stop_event.wait(restart_delay):
                break
            try:
                self._launch()
            except OSError:
                logging.exception("No se pudo reiniciar Photon; se reintentará")

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logging.warning("Photon no terminó a tiempo; forzando cierre")
                process.kill()
                process.wait(timeout=5)
        if self.thread is not None:
            self.thread.join(timeout=5)
        for output_thread in self.output_threads:
            output_thread.join(timeout=2)
        if self.log_thread is not None:
            self.log_queue.put(None)
            self.log_thread.join(timeout=5)

    def is_alive(self) -> bool:
        with self.lock:
            return self.process is not None and self.process.poll() is None

    def snapshot(self) -> dict[str, int | float | bool | None]:
        """Métricas locales para correlacionar caídas con carga HTTP."""
        with self.lock:
            process = self.process
            alive = process is not None and process.poll() is None
            uptime = time.monotonic() - self.launched_at if alive else 0.0
            return {
                "alive": alive,
                "pid": process.pid if alive else None,
                "uptime_seconds": round(max(0.0, uptime), 1),
                "launch_count": self.launch_count,
                "unexpected_exits": self.unexpected_exits,
                "last_exit_code": self.last_exit_code,
                "output_lines": self.output_lines,
                "packet_logging_enabled": self.log_packet_events,
                "suppressed_packet_lines": self.suppressed_packet_lines,
                "pending_output_lines": self.log_queue.qsize(),
            }
