"""Bloqueo de instancia y configuración operativa del proceso principal."""

from __future__ import annotations

import logging
import os
import queue
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler

from .config import DATA, LOG_FILE


class ClosingQueueListener(QueueListener):
    """QueueListener que libera sus archivos al completar el drenado."""

    def stop(self) -> None:
        super().stop()
        for handler in self.handlers:
            handler.flush()
            handler.close()


def acquire_single_instance_lock():
    """Impide que dos backends separen el estado HTTP/Photon entre procesos."""
    lock_path = DATA / "server.instance.lock"
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        raise SystemExit(
            "Ya existe una instancia de MSA activa. No vuelvas a abrir start.bat."
        )
    return handle


def configure_logging() -> ClosingQueueListener:
    """Configura logging no bloqueante y devuelve su listener de escritura."""
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logfile = RotatingFileHandler(
        LOG_FILE, maxBytes=20 * 1024 * 1024, backupCount=5,
        encoding="utf-8",
    )
    logfile.setFormatter(formatter)
    # SimpleQueue no impone un límite ni descarta registros. Los hilos HTTP y
    # el lector de Photon solo encolan; rotación, consola y disco ocurren en el
    # listener dedicado.
    log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    listener = ClosingQueueListener(
        log_queue, console, logfile, respect_handler_level=True
    )
    queue_handler = QueueHandler(log_queue)
    queue_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(
        level=logging.INFO, handlers=[queue_handler], force=True
    )
    listener.start()
    logging.info("%s", "=" * 72)
    logging.info("Nueva sesión del servidor MSA")
    return listener
