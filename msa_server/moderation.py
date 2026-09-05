"""Local, persistent account sanctions; never infer identity from IP/model.

Only the administration CLI writes this registry. Requests read atomic snapshots
so sanctions take effect without restarting the server.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import storage

_LOCK = threading.RLock()


def load():
    path = storage.DATA / "bans.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "bans": {}, "history": []}
    if (not isinstance(state, dict) or state.get("version") != 1
            or not isinstance(state.get("bans"), dict)
            or not isinstance(state.get("history"), list)):
        raise ValueError("Invalid moderation registry; restore a verified backup")
    for account, record in state["bans"].items():
        if (not isinstance(account, str) or not isinstance(record, dict)
                or type(record.get("active")) is not bool
                or not all(isinstance(record.get(k), str)
                           for k in ("reason", "operator", "at"))):
            raise ValueError("Invalid moderation record")
    return state


def check(headers, registry, account=None):
    """Reject only exact known credentials/accounts, including revoked tokens.

    Checking both headers does not associate their accounts or propagate bans:
    an attacker cannot permanently ban somebody by supplying their cookie.
    """
    from .accounts import AccountError, credential
    from .identity import device_cookie
    bans = load()["bans"]
    candidates = {account} if account else set()
    keys = {credential(headers)}
    cookie = device_cookie(headers)
    if cookie:
        keys.add(credential({"terminal_id": cookie}))
    for key in keys:
        device = registry["devices"].get(key)
        if device:
            candidates.add(device["account"])
    if any(bans.get(candidate, {}).get("active", False)
           for candidate in candidates):
        raise AccountError()


@contextmanager
def _writer_lock():
    """Serialize independent admin processes on Windows and Unix."""
    with _LOCK:
        storage.DATA.mkdir(parents=True, exist_ok=True)
        with (storage.DATA / "moderation.lock").open("a+b") as handle:
            handle.seek(0, 2)
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
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def resolve(target, state):
    from . import accounts
    from .identity import public_player_id
    registry = accounts._load()
    known = {d["account"] for d in registry["devices"].values()}
    known.update(state["bans"])
    matches = [a for a in known if target in (a, public_player_id(a))]
    if len(matches) != 1:
        raise ValueError("Unknown or ambiguous player ID; use an exact msa-... ID with registered credentials")
    return matches[0]


def administer(action, target=None, reason="", operator=""):
    """No HTTP administration surface and no writes to accounts.json."""
    from .identity import public_player_id
    if action not in {"ban", "unban", "status", "list"}:
        raise ValueError("Unknown moderation action")
    if action in {"ban", "unban"}:
        if not reason.strip() or len(reason) > 1000 or not operator.strip() or len(operator) > 200:
            raise ValueError("Reason (1-1000 characters) and operator (1-200) are required")
        with _writer_lock():
            state = load()
            account = resolve(target, state)
            record = {"active": action == "ban", "reason": reason.strip(),
                      "operator": operator.strip(),
                      "at": datetime.now(timezone.utc).isoformat()}
            state["bans"][account] = record
            state["history"].append({"player": public_player_id(account),
                                     "action": action, **record})
            storage.atomic_write(storage.DATA / "bans.json",
                                 json.dumps(state, ensure_ascii=False).encode("utf-8"))
            return {"player": public_player_id(account), **record}
    state = load()
    if action == "list":
        return [{"player": public_player_id(a), **r}
                for a, r in sorted(state["bans"].items()) if r["active"]]
    account = resolve(target, state)
    return {"player": public_player_id(account),
            **state["bans"].get(account, {"active": False})}
