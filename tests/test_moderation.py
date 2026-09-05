"""Moderation tests use isolated state, never real player files."""
import base64
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import admin
from msa_server import accounts, moderation, storage
from msa_server.identity import public_player_id


class ModerationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = patch.object(storage, "DATA", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.a = {"terminal_id": "a" * 32}
        self.b = {"terminal_id": "b" * 32}
        accounts.bind_legacy(self.a, "client-a")
        accounts.bind_legacy(self.b, "client-b")

    def ban(self, target="client-a"):
        return moderation.administer("ban", public_player_id(target), "trampas", "test")

    def password(self, client="client-a", headers=None):
        accounts.save_backup(client, {"save_data": [base64.b64encode(b"fixture").decode()]})
        return accounts.issue(headers or self.a, client, {"secret_key": ["key"]})["password"]

    def transfer(self, password, headers=None):
        return accounts.transfer(headers or {}, {"password": [password], "secret_key": ["destination"]},
                                 "192.0.2.1", lambda _: False)

    def test_ban_persists_and_unban_preserves_credentials_and_history(self):
        password = self.password()
        before = (self.root / "accounts.json").read_bytes()
        self.ban()
        self.assertEqual((self.root / "accounts.json").read_bytes(), before)
        with self.assertRaises(accounts.AccountError):
            accounts.lookup(self.a)
        self.assertEqual(accounts.lookup(self.b), "client-b")
        self.assertTrue(json.loads((self.root / "bans.json").read_text())["bans"]["client-a"]["active"])
        self.assertEqual(len(moderation.administer("list")), 1)
        moderation.administer("unban", public_player_id("client-a"), "revisión", "test")
        self.assertEqual(accounts.lookup(self.a), "client-a")
        self.assertFalse(moderation.administer("status", "client-a")["active"])
        self.assertEqual(len(moderation.load()["history"]), 2)
        self.assertEqual(moderation.administer("list"), [])
        self.assertIn("terminal_id", self.transfer(password))

    def test_banned_source_password_cannot_transfer_to_clean_installation(self):
        password = self.password()
        self.ban()
        with self.assertRaises(accounts.AccountError):
            self.transfer(password)
        with self.assertRaises(accounts.AccountError):
            accounts.issue(self.a, "client-a", {"secret_key": ["key"]})

    def test_banned_destination_cannot_import_innocent_account(self):
        password = self.password("client-b", self.b)
        self.ban()
        with self.assertRaises(accounts.AccountError):
            self.transfer(password, self.a)
        self.assertEqual(accounts.lookup(self.b), "client-b")
        self.assertFalse(moderation.administer("status", "client-b")["active"])

    def test_cookie_does_not_permanently_contaminate_another_account(self):
        self.ban()
        mixed = {**self.b, "Cookie": "msa_device=" + "a" * 32}
        with self.assertRaises(accounts.AccountError):
            accounts.lookup(mixed)
        self.assertEqual(accounts.lookup(self.b), "client-b")
        with self.assertRaises(accounts.AccountError):
            accounts.lookup({"Cookie": "msa_device=" + "a" * 32})

    def test_shared_model_and_ip_are_not_ban_identifiers(self):
        self.ban()
        for headers in (self.b, {"terminal_id": "new-installation"}):
            headers = {**headers, "devicemodel": "same-phone", "osversion": "same",
                       "X-Forwarded-For": "192.0.2.1"}
            accounts.bind_legacy(headers, "client-innocent")
            accounts.lookup(headers)

    def test_ban_blocks_all_account_credentials_including_revoked_ones(self):
        result = self.transfer(self.password())
        self.ban()
        for headers in (self.a, {"terminal_id": result["terminal_id"]}):
            with self.assertRaises(accounts.AccountError):
                accounts.lookup(headers)
            with self.assertRaises(accounts.AccountError):
                self.transfer("arbitrary", headers)

    def test_request_guard_rechecks_ban_after_identity_resolution(self):
        client = accounts.lookup(self.a)
        self.ban()
        with self.assertRaises(accounts.AccountError):
            with accounts.request_guard(self.a, client):
                self.fail("Banned request reached gameplay")

    def test_invalid_registry_fails_closed(self):
        for value in (b"broken", b'{}', b'{"version":1,"bans":{"x":{}},"history":[]}'):
            storage.atomic_write(self.root / "bans.json", value)
            with self.assertRaises(ValueError):
                accounts.lookup(self.a)
            with self.assertRaises(ValueError):
                self.ban()
            self.assertEqual((self.root / "bans.json").read_bytes(), value)

    def test_unknown_target_and_missing_reason_never_write_bans(self):
        with self.assertRaises(ValueError):
            moderation.administer("ban", "msa-unknown", "reason", "operator")
        with self.assertRaises(ValueError):
            moderation.administer("ban", "client-a", "", "operator")
        self.assertFalse((self.root / "bans.json").exists())

    def test_cli_commands_and_errors(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(admin.main(["ban", public_player_id("client-a"),
                                         "--reason", "trampas", "--operator", "test"]), 0)
        self.assertTrue(json.loads(output.getvalue())["active"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(admin.main(["status", "msa-unknown"]), 1)

    def test_matchmaking_drops_banned_waiting_room_not_innocent_account(self):
        from msa_server.matchmaking import OnlineMatchmaker
        matcher = OnlineMatchmaker()
        old = matcher.enter("1on1", "client-a")
        self.ban()
        new = matcher.enter("1on1", "client-b")
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(new["clients"], ["client-b"])
        self.assertIsNone(matcher.room("1on1", old["id"]))
        self.assertFalse(moderation.administer("status", "client-b")["active"])
