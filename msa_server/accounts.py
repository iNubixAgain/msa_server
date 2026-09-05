"""Account transfer, opaque backups and revocable device credentials.

The registry is private, fail-closed JSON. Account IDs and player files never
move. Request locks serialize a transfer with in-flight account operations.
"""
from __future__ import annotations

import hashlib
import base64
import binascii
import hmac
import json
import secrets
import threading
import time
from contextlib import contextmanager

from . import storage

_LOCK = threading.RLock()
_REQUEST_LOCKS: dict[str, threading.RLock] = {}
SENSITIVE_ROUTES = {"/title/takeover", "/title/transfer",
                    "/setting/upload_save_data"}
RETRY_TTL = 10 * 60
MAX_BACKUP_BYTES = 4 * 1024 * 1024
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PASSWORD_LENGTH = 12


class AccountError(Exception):
    def __init__(self, code: int = 401):
        self.code = code
        super().__init__("Account operation rejected")


def credential(headers) -> str:
    from .identity import device_cookie, INVALID_TERMINAL_IDS
    value = headers.get("terminal_id", "").strip()
    if value.lower() in INVALID_TERMINAL_IDS:
        value = device_cookie(headers)
    if value.lower().startswith("msa-device-"):
        value = value[11:]
    if len(value) == 32 and all(c in "0123456789abcdefABCDEF" for c in value):
        value = value.lower()
    return hashlib.sha256(value.encode()).hexdigest() if value else ""


def _load() -> dict:
    path = storage.DATA / "accounts.json"
    if not path.exists():
        return {"version": 1, "key": secrets.token_hex(32), "devices": {},
                "transferred": [], "grants": {}, "attempts": {}}
    # Never fall back to an empty registry: that would restore revoked access.
    state = json.loads(path.read_text(encoding="utf-8"))
    if (state.get("version") != 1 or not isinstance(state.get("devices"), dict)
            or not isinstance(state.get("grants"), dict)
            or not isinstance(state.get("attempts"), dict)
            or not isinstance(state.get("transferred"), list)
            or len(bytes.fromhex(state.get("key", ""))) != 32):
        raise ValueError("Invalid account registry")
    return state


def _save(state: dict) -> None:
    storage.atomic_write(storage.DATA / "accounts.json",
                         json.dumps(state, separators=(",", ":")).encode())


def lookup(headers) -> str | None:
    key = credential(headers)
    with _LOCK:
        device = _load()["devices"].get(key)
        if device is not None:
            if not device["active"]:
                raise AccountError()
            return device["account"]
    return None


def bind_legacy(headers, client: str) -> str:
    key = credential(headers)
    with _LOCK:
        state = _load()
        if key in state["devices"]:
            device = state["devices"][key]
            if not device["active"]:
                raise AccountError()
            return device["account"]
        if client in state["transferred"]:
            raise AccountError()
        if key:
            state["devices"][key] = {"account": client, "active": True}
            _save(state)
    return client


def _request_lock(client):
    with _LOCK:
        return _REQUEST_LOCKS.setdefault(client, threading.RLock())


@contextmanager
def request_guard(headers, client):
    with _request_lock(client):
        resolved = lookup(headers)
        if resolved is not None and resolved != client:
            raise AccountError()
        yield


def _field(params, name, limit=256):
    values = params.get(name, [])
    if len(values) != 1 or not values[0] or len(values[0]) > limit:
        raise AccountError(400)
    return values[0]


def save_backup(client, params):
    value = _field(params, "save_data", MAX_BACKUP_BYTES)
    if len(value.encode("utf-8")) > MAX_BACKUP_BYTES:
        raise AccountError(400)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise AccountError(400) from None
    if not decoded:
        raise AccountError(400)
    # Preserve opaque native data byte-for-byte; decoding belongs to the client.
    storage.atomic_write(storage.client_data_file(client, "save_data.json"),
                         json.dumps({"save_data": value}).encode())


def _backup(client):
    path = storage.client_data_file(client, "save_data.json")
    if not path.exists():
        raise AccountError(409)
    value = json.loads(path.read_text(encoding="utf-8"))["save_data"]
    if not isinstance(value, str) or not value:
        raise AccountError(409)
    return value


def _verifier(secret, salt):
    return hashlib.scrypt(secret.encode(), salt=bytes.fromhex(salt),
                          n=16384, r=8, p=1).hex()


def _bind_or_verify_secret(device: dict, secret: str) -> None:
    """Bind legacy devices once; afterwards authenticate their local key."""
    salt = device.get("secret_salt")
    verifier = device.get("secret_verifier")
    if salt is None and verifier is None:
        salt = secrets.token_hex(16)
        device["secret_salt"] = salt
        device["secret_verifier"] = _verifier(secret, salt)
        return
    if (not isinstance(salt, str) or not isinstance(verifier, str)
            or not hmac.compare_digest(_verifier(secret, salt), verifier)):
        raise AccountError(10103001)


def issue(headers, client, params):
    device_key = credential(headers)
    if not device_key:
        raise AccountError()
    secret = _field(params, "secret_key")
    _backup(client)
    password = "".join(
        secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH)
    )
    with _LOCK:
        state = _load()
        device = state["devices"].get(device_key)
        if (device is None or not device.get("active")
                or device.get("account") != client):
            raise AccountError()
        _bind_or_verify_secret(device, secret)
        now = time.time()
        # One pending password per account. Unused passwords do not expire;
        # consumed retry receipts can be discarded after their short window.
        state["grants"] = {
            key: value for key, value in state["grants"].items()
            if value["account"] != client
            and not (value.get("used")
                     and value.get("retry_until", 0) <= now)
        }
        state["grants"][hashlib.sha256(password.encode()).hexdigest()] = {
            "account": client, "used": False,
        }
        _save(state)
    return {"password": password}


def _rate_limit(state, source_ip):
    now = time.time()
    attempts = {key: value for key, value in state["attempts"].items()
                if value["until"] > now}
    key = hashlib.sha256(source_ip.encode()).hexdigest()
    bucket = attempts.setdefault(key, {"count": 0, "until": now + 60})
    if bucket["count"] >= 5 or len(attempts) > 4096:
        raise AccountError(429)
    bucket["count"] += 1
    state["attempts"] = attempts
    _save(state)


def transfer(headers, params, source_ip, busy):
    password = _field(params, "password")
    # This is the destination installation's own key, not the source key.
    # A fresh client legitimately has no terminal_id until this call succeeds.
    destination_secret = _field(params, "secret_key")
    key = hashlib.sha256(password.encode()).hexdigest()
    with _LOCK:
        state = _load()
        _rate_limit(state, source_ip)
        grant = state["grants"].get(key)
        if grant is None:
            raise AccountError(10103001)
        _rate_limit(state, "grant:" + key)
        account = grant["account"]
    with _request_lock(account), _LOCK:
        state = _load()
        grant = state["grants"].get(key)
        if grant is None:
            raise AccountError(10103001)
        if grant["used"]:
            salt = grant.get("destination_salt", "")
            verifier = grant.get("destination_verifier", "")
            if (grant["retry_until"] <= time.time()
                    or not salt or not verifier
                    or not hmac.compare_digest(
                        _verifier(destination_secret, salt), verifier)):
                raise AccountError(10103001)
        else:
            if busy(account):
                raise AccountError(409)
            _backup(account)
            destination_salt = secrets.token_hex(16)
            grant.update(
                used=True,
                destination_salt=destination_salt,
                destination_verifier=_verifier(
                    destination_secret, destination_salt),
                retry_until=time.time() + RETRY_TTL,
                nonce=secrets.token_hex(16),
            )
            for device in state["devices"].values():
                if device["account"] == account:
                    device["active"] = False
            if account not in state["transferred"]:
                state["transferred"].append(account)
        token = hmac.new(bytes.fromhex(state["key"]),
                         grant["nonce"].encode(), hashlib.sha256).hexdigest()[:32]
        token_key = hashlib.sha256(token.encode()).hexdigest()
        if grant["used"] and token_key in state["devices"]:
            if not state["devices"][token_key]["active"]:
                raise AccountError(10103001)
        state["devices"][token_key] = {
            "account": account,
            "active": True,
            "secret_salt": grant["destination_salt"],
            "secret_verifier": grant["destination_verifier"],
        }
        backup = _backup(account)
        _save(state)
    return {"terminal_id": token, "save_data": backup}
