"""Escritura asíncrona y acotada de capturas HTTP de diagnóstico."""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from .storage import atomic_write


class DiagnosticWriter:
    """Saca del camino crítico HTTP las capturas que no son estado de juego."""

    def __init__(self, max_pending: int = 512) -> None:
        self._queue: queue.Queue[tuple[Path, Path, bytes] | None] = queue.Queue(
            maxsize=max(1, max_pending)
        )
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._written = 0
        self._dropped = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="http-diagnostic-writer", daemon=True
        )
        self._thread.start()

    def submit(self, global_path: Path, client_path: Path, body: bytes) -> bool:
        try:
            self._queue.put_nowait((global_path, client_path, body))
            return True
        except queue.Full:
            with self._lock:
                self._dropped += 1
                dropped = self._dropped
            if dropped == 1 or dropped % 100 == 0:
                logging.warning(
                    "Cola de diagnósticos llena; capturas descartadas=%d", dropped
                )
            return False

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                global_path, client_path, body = item
                atomic_write(global_path, body)
                atomic_write(client_path, body)
                with self._lock:
                    self._written += 1
            except Exception:
                logging.exception("No se pudo guardar una captura HTTP de diagnóstico")
            finally:
                self._queue.task_done()

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._queue.put(None)
        thread.join()
        self._thread = None

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            written = self._written
            dropped = self._dropped
        return {
            "pending": self._queue.qsize(),
            "capacity": self._queue.maxsize,
            "written": written,
            "dropped": dropped,
        }
