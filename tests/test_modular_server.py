from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

import server
from msa_server import news, storage
from msa_server.battle_tactics import (
    BattleTacticsController,
    TACTICS_SUPPORTER_COUNT,
    TACTICS_SHOP,
    TACTICS_UNIT_MAX,
    TACTICS_UNIT_MIN,
    get_battle_tactics,
)
from msa_server.arena import ArenaController, ARENA_LADDER, STARS_REQUIRED, load_arena, save_arena
from msa_server.friend_online import FriendOnlineController
from msa_server.guild_online import GuildOnlineController
from msa_server.identity import public_player_id
from msa_server.matchmaking import OnlineMatchmaker
from msa_server.online import OnlineController, WIN_RANK_POINTS
from msa_server.sessions import UserTracker
from tests.server_fixtures import MASTER_TABLE_EXPECTATIONS


ROOT = Path(__file__).resolve().parent.parent


class ModularServerTests(unittest.TestCase):
    def test_transfer_is_single_winner_and_does_not_revive_old_receipts(self):
        from concurrent.futures import ThreadPoolExecutor
        from msa_server import accounts
        backup = base64.b64encode(b"opaque").decode()
        self.payload("/setting/upload_save_data", "concurrent-owner", {"save_data": backup})
        password = self.payload("/title/takeover", "concurrent-owner", {"secret_key": "key"})["password"]
        fields_by_device = {
            "target-a": {"password": password, "secret_key": "target-key-a"},
            "target-b": {"password": password, "secret_key": "target-key-b"},
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda device: self.raw_payload(
                "/title/transfer", device, fields_by_device[device]),
                ["target-a", "target-b"]))
        winners = [r for r in results if "terminal_id" in r]
        self.assertEqual(len(winners), 1)
        token = winners[0]["terminal_id"]
        winner_key = "target-key-a" if winners[0] is results[0] else "target-key-b"
        password2 = self.payload("/title/takeover", token, {
            "secret_key": winner_key,
        })["password"]
        self.payload("/title/transfer", "target-c", {
            "password": password2, "secret_key": "target-key-c",
        })
        for destination in ("target-a", "target-b"):
            rejected = self.raw_payload("/title/transfer", destination, {
                "password": password, "secret_key": "another-target-key",
            })
            self.assertNotEqual(rejected["response"]["error_code"], 0)
        self.assertNotEqual(self.raw_payload("/title/login", token)["response"]["error_code"], 0)

    def test_transfer_expiry_limits_and_corrupt_registry_fail_closed(self):
        from msa_server import accounts
        backup = base64.b64encode(b"opaque").decode()
        self.payload("/setting/upload_save_data", "expiry-owner", {"save_data": backup})
        password = self.payload("/title/takeover", "expiry-owner", {"secret_key": "key"})["password"]
        with patch.object(accounts.time, "time", return_value=accounts.time.time() + 86401):
            expired = self.raw_payload("/title/transfer", "target", {"password": password, "secret_key": "key"})
            self.assertNotEqual(expired["response"]["error_code"], 0)
        for _ in range(5):
            denied = self.raw_payload("/title/transfer", "target", {"password": "unknown", "secret_key": "wrong"})
        denied = self.raw_payload("/title/transfer", "target", {"password": "unknown", "secret_key": "wrong"})
        self.assertEqual(denied["response"]["error_code"], 429)
        path = self.isolated_data / "accounts.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            accounts.lookup({"terminal_id": "expiry-owner"})

    def test_transfer_preserves_account_and_revokes_old_device(self):
        from msa_server import accounts
        backup = base64.b64encode(b"opaque native fixture").decode()
        original = self.payload("/setting/change_setting", "transfer-old", {
            "name": "TRANSFER PLAYER",
        })["user"]["open_id"]
        self.payload("/setting/upload_save_data", "transfer-old", {"save_data": backup})
        password = self.payload("/title/takeover", "transfer-old", {
            "secret_key": "secret-test",
        })["password"]
        fields = {"password": password, "secret_key": "new-device-secret"}
        result = self.payload("/title/transfer", None, fields)
        self.assertEqual(result["save_data"], backup)
        token = result["terminal_id"]
        self.assertEqual(len(token), 32)
        retry = self.payload("/title/transfer", None, fields)
        self.assertEqual(retry["terminal_id"], token)
        login = self.payload("/title/login", token)
        self.assertEqual(login["user"]["open_id"], original)
        self.assertEqual(login["user"]["name"], "TRANSFER PLAYER")
        for path in ("/title/login", "/setting/change_setting", "/title/regist_user",
                     "/setting/upload_save_data", "/guild/build_guild"):
            with self.subTest(path=path):
                denied = self.raw_payload(path, "transfer-old", {
                    "name": "STOLEN", "save_data": backup,
                })
                self.assertNotEqual(denied["response"]["error_code"], 0)
        denied = self.raw_payload("/title/transfer", "third-device", {
            "password": password, "secret_key": "third-device-secret",
        })
        self.assertNotEqual(denied["response"]["error_code"], 0)
        registry = (self.isolated_data / "accounts.json").read_text()
        for secret in (password, "secret-test", "new-device-secret", token):
            self.assertNotIn(secret, registry)
        # Registry is reloaded from disk; losing process caches cannot revive A.
        accounts._REQUEST_LOCKS.clear()
        self.assertEqual(self.payload("/title/login", token)["user"]["open_id"], original)

    def test_transfer_requires_backup_and_correct_secret_and_idle_account(self):
        backup = base64.b64encode(b"opaque backup").decode()
        missing = self.raw_payload("/title/takeover", "transfer-a", {"secret_key": "key"})
        self.assertNotEqual(missing["response"]["error_code"], 0)
        self.payload("/setting/upload_save_data", "transfer-a", {"save_data": backup})
        password = self.payload("/title/takeover", "transfer-a", {"secret_key": "key"})["password"]
        self.payload("/online_1on1/create_or_enter_room", "transfer-a", {})
        blocked = self.raw_payload("/title/transfer", "transfer-b", {
            "password": password, "secret_key": "destination-key",
        })
        self.assertEqual(blocked["response"]["error_code"], 409)
        self.payload("/title/login", "transfer-a")

    def test_takeover_authenticates_the_current_device_secret(self):
        backup = base64.b64encode(b"opaque backup").decode()
        self.payload("/setting/upload_save_data", "source-device", {
            "save_data": backup,
        })
        first = self.payload("/title/takeover", "source-device", {
            "secret_key": "source-local-secret",
        })
        denied = self.raw_payload("/title/takeover", "source-device", {
            "secret_key": "different-device-secret",
        })
        self.assertEqual(denied["response"]["error_code"], 10103001)
        self.assertIn("password", first)

    def test_transfer_secrets_are_not_captured_and_backup_is_validated(self):
        backup = base64.b64encode(b"private native backup").decode()
        with patch.object(server, "CAPTURE_HTTP_DIAGNOSTICS", True):
            self.payload("/setting/upload_save_data", "private-device", {"save_data": backup})
            self.payload("/title/takeover", "private-device", {"secret_key": "private-key"})
        self.assertFalse(list(self.isolated_data.rglob("*upload_save_data*")))
        self.assertFalse(list(self.isolated_data.rglob("*takeover*")))
        bad = self.raw_payload("/setting/upload_save_data", "private-device", {"save_data": "not base64!"})
        self.assertEqual(bad["response"]["error_code"], 400)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.isolated_data = Path(self.temp.name)
        self.old_server_data = server.DATA
        self.old_storage_data = storage.DATA
        self.old_news_data = news.DATA
        self.old_matchmaker = server.MATCHMAKER
        self.old_online = server.ONLINE
        self.old_arena = server.ARENA
        self.old_friend_online = server.FRIEND_ONLINE
        self.old_guild_online = server.GUILD_ONLINE
        self.old_battle_tactics = server.BATTLE_TACTICS
        self.old_tracker = server.USER_TRACKER
        server.DATA = self.isolated_data
        storage.DATA = self.isolated_data
        news.DATA = self.isolated_data
        server.MATCHMAKER = OnlineMatchmaker()
        server.BATTLE_TACTICS = BattleTacticsController(server.MATCHMAKER)
        self.test_user_log = self.isolated_data / "UserLog.log"
        self.test_nick_log = self.isolated_data / "Nicknames.log"
        server.USER_TRACKER = UserTracker(
            self.test_user_log,
            nickname_log_path=self.test_nick_log,
            timeout=1.0,
        )
        server.ONLINE = OnlineController(server.MATCHMAKER, server.USER_TRACKER)
        server.ARENA = ArenaController(server.MATCHMAKER, server.USER_TRACKER)
        server.FRIEND_ONLINE = FriendOnlineController(
            server.MATCHMAKER, server.SOCIAL, server.USER_TRACKER
        )
        server.GUILD_ONLINE = GuildOnlineController(
            server.MATCHMAKER, server.SOCIAL, server.USER_TRACKER
        )
        server.CORE.tracker = server.USER_TRACKER

        self.httpd = server.RobustThreadingHTTPServer(
            ("127.0.0.1", 0), server.Handler
        )
        self.port = self.httpd.server_address[1]
        self.worker = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.worker.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.worker.join(timeout=5)
        server.USER_TRACKER.stop()
        server.USER_TRACKER = self.old_tracker
        server.CORE.tracker = self.old_tracker
        server.ONLINE.tracker = self.old_tracker
        server.DATA = self.old_server_data
        storage.DATA = self.old_storage_data
        news.DATA = self.old_news_data
        server.MATCHMAKER = self.old_matchmaker
        server.ONLINE = self.old_online
        server.ARENA = self.old_arena
        server.FRIEND_ONLINE = self.old_friend_online
        server.GUILD_ONLINE = self.old_guild_online
        server.BATTLE_TACTICS = self.old_battle_tactics
        self.temp.cleanup()

    def payload(self, path: str, terminal: str | None = "device-a",
                fields: dict | None = None) -> dict:
        payload = self.raw_payload(path, terminal, fields)
        self.assertEqual(payload.get("response", {}).get("error_code"), 0)
        return payload

    def raw_payload(self, path: str, terminal: str | None = "device-a",
                    fields: dict | None = None) -> dict:
        data = None
        headers = {} if terminal is None else {"terminal_id": terminal}
        if fields is not None:
            data = urllib.parse.urlencode(fields, doseq=True).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
        return payload

    def test_core_gameplay_and_assets(self) -> None:
        for path in (
            "/__offline_status", "/title/get_pack_info", "/title/get_file_list",
            "/title/login", "/top_menu/top", "/setting/top", "/build_up/top",
            "/unit_up/top", "/gacha/top", "/world_map/top", "/story/top",
            "/friend/top", "/training/top", "/event/marathon/top",
        ):
            self.payload(path)
        pack_info = self.payload("/title/get_pack_info")
        self.assertEqual(pack_info["url"], f"http://127.0.0.1:{self.port}/snkp/msatk/prod/pack/0/")
        master = self.payload("/title/get_master_table?table%5B%5D=m_item")
        self.assertTrue(master["table"])
        self.assertIn("m_item", master["table"][0])
        file_list = self.payload("/title/get_file_list")
        self.assertEqual(file_list["master_ver"], 7130017)
        usable = next(
            entry for entry in file_list["file_list"]
            if "m_unit_usable" in entry["url"]
        )
        self.assertEqual(
            (usable["file_name"], usable["hash"], usable["file_size"]),
            ("2e34864932eb516ffb697b57d495345f",
             "d42fb7e02a4942a94657aba70422160b", 9152),
        )

    def test_competitive_endpoints_receive_a_progressing_server_clock(self) -> None:
        clocks = (
            {"error_code": 0, "now_time": "2026-09-02 01:00:00",
             "server_time": "2026-09-02 01:00:00"},
            {"error_code": 0, "now_time": "2026-09-02 01:00:01",
             "server_time": "2026-09-02 01:00:01"},
        )
        with patch.object(server, "response_clock", side_effect=clocks):
            arena = self.payload("/arena/top", "clock-arena")
            tactics = self.payload("/battle_tactics/top", "clock-tactics")
        self.assertEqual(arena["response"]["server_time"], clocks[0]["server_time"])
        self.assertEqual(tactics["response"]["server_time"], clocks[1]["server_time"])
        self.assertGreater(
            tactics["response"]["server_time"],
            arena["response"]["server_time"],
        )

    def test_battle_is_asynchronous_persistent_and_has_unlimited_attempts(self) -> None:
        player_a = "battle-player-a"
        player_b = "battle-player-b"

        top_b = self.payload("/off_1on1/top", player_b)
        self.assertEqual(top_b["off_1on1_btl"]["chance"], 5)
        self.payload("/off_1on1/set_defence_deck", player_b, {
            "cover": 2,
            "deck_unit[]": [24, 38, 39, 40, 41, 43, 44, 47, 50, 69],
            "deck_attribute[]": [0] * 10,
        })
        reentered_b = self.payload("/off_1on1/top", player_b)
        self.assertEqual(reentered_b["user"]["deck"][3]["cover"], 2)
        self.assertEqual(
            reentered_b["user"]["deck"][3]["unit_ids"],
            [24, 38, 39, 40, 41, 43, 44, 47, 50, 69],
        )

        top_a = self.payload("/off_1on1/top", player_a)
        mode = top_a["off_1on1_btl"]
        self.assertEqual(mode["chance"], 5)
        self.assertEqual(mode["battle_point"], 0)
        self.assertEqual(len(mode["opponent"]), 3)
        rival = mode["opponent"][0]
        self.assertEqual(rival["person"]["name"],
                         top_b["user"]["name"])
        refreshed = self.payload("/off_1on1/get_enemy", player_a)
        self.assertIn("opponent", refreshed)
        self.assertEqual(len(refreshed["opponent"]), 3)
        self.assertNotIn("off_1on1_btl", refreshed)

        start = self.payload("/off_1on1/battle_start", player_a, {
            "enemy_id": rival["person"]["user"],
            "opponent_rank": rival["rank"],
            "cover": 0, "deck_no": 1,
            "unit_ids[]": [16, 17, 18, 19, 27, 2, 3, 6, 7, 8],
            "unit_level[]": [50] * 10,
        })
        self.assertNotIn("off_1on1_btl", start)
        self.assertEqual(start["enemy_deck"]["cover"], 2)
        self.assertEqual(
            [unit["unit_id"] for unit in start["enemy_deck"]["unit_param"]],
            [24, 38, 39, 40, 41, 43, 44, 47, 50, 69],
        )

        won = self.payload("/off_1on1/battle_win", player_a, {
            "enemy_id": rival["person"]["user"], "token": start["token"],
        })
        self.assertEqual(won["off_1on1_btl"]["battle_point"], 100)
        self.assertEqual(won["off_1on1_btl"]["chance"], 5)

        # Repetir el mismo resultado no duplica puntos y los intentos nunca
        # se consumen ni requieren medallas.
        duplicate = self.payload("/off_1on1/battle_win", player_a, {
            "enemy_id": rival["person"]["user"], "token": start["token"],
        })
        self.assertEqual(duplicate["off_1on1_btl"]["battle_point"], 100)
        self.assertEqual(duplicate["off_1on1_btl"]["chance"], 5)

        ranking = self.payload("/off_1on1/ranking", player_a, {"kind": 1})
        self.assertEqual(ranking["online_score"], 100)
        self.assertTrue(ranking["top50"])
        self.assertTrue(
            (self.isolated_data / "battle_ladder.json").is_file()
        )

    def test_team_battle_persists_three_decks_and_displays_three_rivals(self) -> None:
        defender = "team-battle-defender"
        attacker = "team-battle-attacker"
        self.payload("/off_team/top", defender)
        fields = {}
        expected_decks = []
        for slot, first_unit in enumerate((16, 24, 34), 1):
            units = [first_unit + offset for offset in range(10)]
            expected_decks.append(units)
            fields[f"cover_{slot}"] = slot - 1
            fields[f"deck_unit_{slot}[]"] = units
            fields[f"deck_attribute_{slot}[]"] = [0] * 10
        self.payload("/off_team/set_defence_deck", defender, fields)

        reentered = self.payload("/off_team/top", defender)
        for offset, units in enumerate(expected_decks):
            self.assertEqual(
                reentered["user"]["deck"][4 + offset]["unit_ids"], units
            )

        top = self.payload("/off_team/top", attacker)
        mode = top["off_team_btl"]
        self.assertEqual(len(mode["opponent"]), 3)
        rival = mode["opponent"][0]
        self.assertEqual(rival["person"]["name"], reentered["user"]["name"])

        prepared = self.payload("/off_team/prepare_battle_start", attacker, {
            "enemy_id": rival["person"]["user"],
        })
        self.assertEqual(len(prepared["enemy_deck"]), 3)
        self.assertEqual(
            [unit["unit_id"] for unit in prepared["enemy_deck"][0]["unit_param"]],
            expected_decks[0],
        )
        started = self.payload("/off_team/battle_start", attacker, {
            "enemy_id": rival["person"]["user"],
            "opponent_rank": rival["rank"],
            **{f"cover_{slot}": slot - 1 for slot in range(1, 4)},
            **{f"deck_unit_{slot}[]": expected_decks[slot - 1]
               for slot in range(1, 4)},
            **{f"unit_level_{slot}[]": [50] * 10 for slot in range(1, 4)},
        })
        won = self.payload("/off_team/battle_win", attacker, {
            "enemy_id": rival["person"]["user"], "token": started["token"],
        })
        self.assertEqual(won["off_team_btl"]["battle_point"], 100)
        self.assertEqual(won["off_team_btl"]["chance"], 5)

    def test_every_published_master_table_is_exactly_deliverable(self) -> None:
        catalog = json.loads(
            (ROOT / "server_content/file_list.json").read_text(encoding="utf-8")
        )
        for entry in catalog:
            query = urllib.parse.urlsplit(entry["url"]).query
            table = urllib.parse.parse_qs(query)["table[]"][0]
            response = server.ASSETS_CONTROLLER.handle(
                "/title/get_master_table", {"table[]": [table]},
                "catalog-audit", {"error_code": 0},
            )
            self.assertIsNotNone(response, table)
            payload = json.loads(response.body)
            self.assertEqual(list(payload["table"][0]), [table])
            body = base64.b64decode(payload["table"][0][table])
            self.assertEqual(len(body), entry["file_size"], table)
            self.assertEqual(hashlib.md5(body).hexdigest(), entry["hash"], table)

    def test_new_registration_keeps_identity_and_safe_default_deck(self) -> None:
        self.payload("/setting/change_setting", "msa-offline-local", {
            "name": "SHARED LEGACY PROFILE", "language": 3,
        })
        first = self.payload("/title/regist_user", "msa-offline-local")
        second = self.payload("/title/regist_user", "msa-offline-local")
        self.assertEqual(len(first["terminal_id"]), 32)
        self.assertEqual(len(second["terminal_id"]), 32)
        self.assertTrue(all(char in "0123456789abcdef" for char in first["terminal_id"]))
        self.assertTrue(all(char in "0123456789abcdef" for char in second["terminal_id"]))
        self.assertNotEqual(second["terminal_id"], first["terminal_id"])

        top = self.payload("/top_menu/top", first["terminal_id"])
        self.assertEqual(
            top["user"]["deck"][0]["unit_ids"],
            [16, 17, 18, 19, 27, 2, 3, 6, 7, 8],
        )
        other = self.payload("/top_menu/top", second["terminal_id"])
        self.assertNotEqual(top["user"]["open_id"], other["user"]["open_id"])
        self.assertNotEqual(top["user"]["name"], "SHARED LEGACY PROFILE")
        self.assertNotEqual(other["user"]["name"], "SHARED LEGACY PROFILE")

        self.payload("/setting/change_setting", first["terminal_id"], {
            "name": "FIRST INSTALLATION", "language": 3,
        })
        self.assertEqual(
            self.payload("/setting/top", first["terminal_id"])
            ["user"]["name"],
            "FIRST INSTALLATION",
        )
        self.assertNotEqual(
            self.payload("/setting/top", second["terminal_id"])
            ["user"]["name"],
            "FIRST INSTALLATION",
        )

        from msa_server.identity import resolve_client_identity
        token = first["terminal_id"]
        by_terminal = resolve_client_identity(
            {"terminal_id": first["terminal_id"]}, "127.0.0.10"
        )
        by_cookie = resolve_client_identity(
            {"terminal_id": "msa-offline-local",
             "Cookie": f"msa_device={token}"},
            "127.0.0.11",
        )
        self.assertEqual(by_terminal.client, by_cookie.client)

    def test_any_first_request_repairs_complete_server_state(self) -> None:
        terminal = "recent-device"
        self.payload("/title/get_pack_info", terminal)
        client_dirs = list((self.isolated_data / "clients").iterdir())
        self.assertEqual(len(client_dirs), 1)
        client_dir = client_dirs[0]
        expected = {
            "profile.json", "user_deck.json", "user_deck.backup.json",
            "user_units.json", "online.json", "ultimate_mission.json",
            "battle_tactics.json",
        }
        self.assertTrue(expected.issubset({path.name for path in client_dir.iterdir()}))

        (client_dir / "profile.json").write_text("{}", encoding="utf-8")
        (client_dir / "user_deck.json").write_text("{bad", encoding="utf-8")
        self.payload("/top_menu/top", terminal)
        repaired_profile = json.loads(
            (client_dir / "profile.json").read_text(encoding="utf-8")
        )
        repaired_deck = json.loads(
            (client_dir / "user_deck.json").read_text(encoding="utf-8")
        )
        self.assertEqual(repaired_profile["language"], 8)
        self.assertIsInstance(repaired_deck, list)
        self.assertGreaterEqual(len(repaired_deck), 26)

    def test_inventory_excludes_incompatible_legacy_units(self) -> None:
        incompatible = {67, 103, 155, 175, 294, 301, 302, 413, 936,
                        *range(3001, 3010)}
        build_up = self.payload("/build_up/top", "device-extra-units")
        unit_up = self.payload("/unit_up/top", "device-extra-units")
        direct = self.raw_payload("/user/get_units", "device-extra-units")

        def ids(payload: dict) -> set[int]:
            units = payload["user"]["units"] if "user" in payload else payload
            return {entry["param"]["unit_id"] for entry in units["unit"]}

        self.assertEqual(ids(build_up), ids(unit_up))
        self.assertEqual(ids(build_up), ids(direct))
        self.assertFalse(incompatible & ids(build_up))
        self.assertIn(2, ids(build_up))

    def test_buff_items_update_and_persist_special_unit_stats(self) -> None:
        terminal = "device-buff-items"

        def unit(payload: dict, unit_id: int) -> dict:
            return next(
                entry["param"] for entry in payload["user"]["units"]["unit"]
                if entry["param"]["unit_id"] == unit_id
            )

        before = self.payload("/unit_up/top", terminal)
        self.assertEqual(
            unit(before, 285)["status_seed"],
            [10, 10, 10, 10, 0, 0, 0, 0, 0],
        )

        upgraded = self.payload(
            "/unit_up/status_seed_up", terminal, {
                "unit_id": 285,
                "type[]": [5, 6, 8, 9],
                "up_level[]": [10, 10, 10, 10],
            },
        )
        expected = [10, 10, 10, 10, 10, 10, 0, 10, 10]
        self.assertEqual(unit(upgraded, 285)["status_seed"], expected)
        self.assertEqual(
            unit(self.payload("/unit_up/top", terminal), 285)["status_seed"],
            expected,
        )
        self.assertEqual(
            unit(self.payload("/unit_up/top", "other-buff-user"), 285)
            ["status_seed"],
            [10, 10, 10, 10, 0, 0, 0, 0, 0],
        )


    def test_ultimate_mission_contract_and_battle_flow(self) -> None:
        terminal = "device-ultimate"
        top = self.payload("/ultimate_mission/top", terminal)
        mission = top["ultimate_mission"]
        self.assertEqual(set(mission), {
            "chance", "reset_num", "max_reset_num", "next_update_time",
            "attack_affection", "deck",
        })
        self.assertEqual(mission["chance"], 3)
        self.assertGreater(mission["next_update_time"], "2090-01-01 00:00:00")
        self.assertEqual(
            [deck["affection"] for deck in mission["deck"]],
            [0, 1, 2, 3, 4],
        )
        self.assertEqual(
            [deck["deck_id"] for deck in mission["deck"]],
            [1, 11, 21, 31, 41],
        )

        # Migra el estado defectuoso que versiones anteriores guardaban con
        # el mazo PF (deck_id=1) para todas las armadas.
        persisted_file = next(
            self.isolated_data.glob("clients/*/ultimate_mission.json")
        )
        legacy = json.loads(persisted_file.read_text(encoding="utf-8"))
        for deck in legacy["deck"]:
            deck["deck_id"] = 1
        persisted_file.write_text(json.dumps(legacy), encoding="utf-8")
        repaired = self.payload("/ultimate_mission/top", terminal)[
            "ultimate_mission"
        ]
        self.assertEqual(
            [deck["deck_id"] for deck in repaired["deck"]],
            [1, 11, 21, 31, 41],
        )
        mission = repaired

        start = self.payload(
            "/ultimate_mission/battle_start", terminal,
            {"stage_id": "1", "deck_kind": "0"},
        )
        self.assertEqual(start["continue_medal"], 0)
        self.assertEqual(start["relief_supplies_contents"], {"contents": []})

        for route in ("battle_win", "battle_lose", "battle_retire", "raid"):
            result = self.payload(f"/ultimate_mission/{route}", terminal)
            self.assertEqual(result["ultimate_mission"], mission)

        persisted_files = list(
            self.isolated_data.glob("clients/*/ultimate_mission.json")
        )
        self.assertEqual(len(persisted_files), 1)
        self.assertEqual(json.loads(persisted_files[0].read_text()), mission)

    def test_gacha_play_returns_visual_unit_results_without_inventory_write(self) -> None:
        terminal = "device-gacha"
        top = self.payload("/gacha/top", terminal)
        before = top["user"]["units"]
        premium = top["gacha_premium_data"]
        self.assertEqual(len(premium), 2)
        self.assertTrue(all(
            entry["gacha_data"]["gacha_id"] == 14 for entry in premium
        ))

        single = self.payload(
            "/gacha/play", terminal, {"gacha_id": "2", "num": "1"}
        )
        self.assertEqual(len(single["result"]["item"]), 1)
        self.assertEqual(set(single["result"]["item"][0]), {
            "item_id", "quantity", "act",
        })
        from msa_server.gacha import _MEDAL_ITEM_IDS, _UNIT_ITEM_IDS
        self.assertIn(single["result"]["item"][0]["item_id"], _MEDAL_ITEM_IDS)

        multi = self.payload(
            "/gacha/play", terminal, {"gacha_id": "2", "num": "10"}
        )
        self.assertEqual(len(multi["result"]["item"]), 10)
        self.assertEqual(multi["result"]["item_extra"], [])
        self.assertEqual(multi["result"]["box_reward_item"], [])
        self.assertTrue(all(
            item["item_id"] in _UNIT_ITEM_IDS
            for item in multi["result"]["item"]
        ))
        premium_play = self.payload(
            "/gacha/play", terminal, {"gacha_id": "14", "num": "10"}
        )
        self.assertTrue(all(
            item["item_id"] in _UNIT_ITEM_IDS
            for item in premium_play["result"]["item"]
        ))
        self.assertEqual(multi["user"]["units"], before)
        self.assertFalse(list(self.isolated_data.glob("clients/*/gacha*.json")))

    def test_extra_ops_marathon_contract_is_natively_parseable(self) -> None:
        payload = self.payload("/event/marathon/top", "device-extra-ops")
        self.assertEqual(payload["rental_soldier"], {
            "unit": [], "available": 0,
        })
        marathon = payload["marathon"]
        self.assertEqual(len(marathon["world"]), 3)
        self.assertEqual(
            [len(world["stage"]) for world in marathon["world"]],
            [10, 10, 10],
        )
        stages = [
            stage
            for world in marathon["world"]
            for stage in world["stage"]
        ]
        self.assertEqual(stages[0], {
            "stage_id": 0x7E6E, "state": 1, "rank": 0,
        })
        self.assertEqual(stages[-1]["stage_id"], 0x7E8B)
        self.assertTrue(all(
            set(stage) == {"stage_id", "state", "rank"}
            for stage in stages
        ))
        self.assertEqual(marathon["event_point"], 0)
        self.assertEqual(marathon["is_tutorial"], 1)
        self.assertEqual(marathon["is_quest_reward"], 0)

    def test_menu_does_not_publish_an_incompatible_extra_ops(self) -> None:
        menu = self.payload(
            "/top_menu/top", "device-extra-ops-publication"
        )["top_menu"]
        self.assertEqual(menu["latest_event_id"], 0)
        self.assertEqual(menu["latest_event_type"], 0)
        self.assertEqual(menu["event_master"], [])

    def test_marathon_master_artifacts_match_the_verified_server_release(self) -> None:
        offline = ROOT / "server_content"
        catalog = json.loads(
            (offline / "file_list.json").read_text(encoding="utf-8")
        )
        by_table = {
            urllib.parse.parse_qs(
                urllib.parse.urlsplit(entry["url"]).query
            )["table[]"][0]: entry
            for entry in catalog
        }
        for table in ("m_event_master", "m_marathon_setting"):
            expected = MASTER_TABLE_EXPECTATIONS[table]
            self.assertEqual(
                {key: by_table[table][key] for key in expected}, expected
            )
        self.assertFalse(any(
            "table[]=m_melee_" in str(entry.get("url", ""))
            for entry in catalog
        ))

    def test_guild_lifecycle_membership_permissions_and_persistence(self) -> None:
        menu = self.payload("/top_menu/top", "guild-leader")["top_menu"]
        self.assertEqual(menu["latest_event_id"], 0)
        self.assertEqual(menu["latest_event_type"], 0)
        self.assertEqual(menu["event_master"], [])
        guild_top = self.payload("/guild_battle/top", "guild-leader")
        self.assertEqual(guild_top["user"]["guild"], 0)
        self.assertEqual(guild_top["guild"]["guild_info"]["guild_id"], 0)
        self.assertEqual(guild_top["guild"]["member"], [])
        self.assertIn("guild_build_price", guild_top)
        self.assertIn("join_limit_time", guild_top)
        self.assertIn("last_message_date", guild_top)
        self.assertIn("raid", guild_top)
        self.assertIn("rental_soldier", guild_top)
        self.assertIsInstance(guild_top["last_message_date"], dict)
        created = self.payload("/guild/build_guild", "guild-leader", {
            "name": "METAL TEAM", "logo": 7, "profile": "Bienvenidos",
            "join_type": 1, "open": 0, "level": 20,
        })
        guild_id = created["guild_info"]["guild_id"]
        self.assertNotEqual(created["user"]["open_id"], "msa-offline")
        self.assertEqual(created["user"]["guild"], 1)
        self.assertEqual(created["guild_info"]["member_num"], 1)
        self.assertEqual(created["guild_info"]["logo_id"], 7)
        self.assertEqual(created["guild_info"]["open"], 0)
        self.assertEqual(created["guild_info"]["limit_level"], 20)
        self.assertEqual(created["guild_info"]["player"][0]["is_owner"], 1)
        self.assertNotIn("_server_events", created)
        # Snatch Wars está restringido deliberadamente al Guild real 6.
        if guild_id == 6:
            snatch = self.payload("/event/mass/top", "guild-leader")["mass"]
            self.assertEqual(len(snatch["stage"]), 25)
            self.assertEqual((snatch["term_id"], snatch["kind"]), (6, 1))
            self.assertEqual(set(snatch), {
                "term_id", "kind", "stage", "own_stage", "news",
                "guild_info", "deck", "cover", "unit_ids", "role",
                "tactics", "can_reward",
            })
            self.assertEqual(snatch["own_stage"], [])
            self.assertTrue(all(
                1 <= tile["x"] <= 5 and 1 <= tile["y"] <= 5
                for tile in snatch["stage"]
            ))
            self.assertTrue(all(set(tile) == {
                "x", "y", "type", "build_up_msp", "build_up_medal",
                "stage_situation", "chain", "chain_time", "guild_info",
            } for tile in snatch["stage"]))
        self.assertEqual(
            self.payload("/mypage/get_message_info", "guild-leader", {
                "message_type": 2,
            })["message_info"],
            [],
        )
        self.assertEqual(
            self.payload("/guild_battle/top", "guild-leader")
            ["guild"]["guild_info"]["name"],
            "METAL TEAM",
        )
        guild_screen = self.payload("/guild_battle/top", "guild-leader")
        self.assertNotIn("guild_info", guild_screen)
        self.assertNotIn("my_guild_info", guild_screen)
        native = guild_screen["guild"]
        self.assertEqual(native["guild_info"]["guild_id"], guild_id)
        self.assertEqual(native["member"][0]["role"], 1)
        self.assertEqual(native["member"][0]["level"], 50)
        self.assertGreater(native["member"][0]["avatar_id"], 0)
        self.assertNotIn("person", native["member"][0])

        found = self.payload("/guild/search_guild", "guild-member", {
            "name": "metal",
        })
        self.assertEqual(found["num"], 1)
        self.assertEqual(found["guild"][0]["guild_id"], guild_id)
        self.assertEqual(found["applicant"][0]["logo_id"], 7)
        requested = self.payload("/guild/request_enrollment", "guild-member", {
            "guild_id": guild_id,
        })
        self.assertEqual(len(requested["guild"]["applicant"]), 1)

        member_id = requested["guild"]["applicant"][0]["user"]
        approved = self.payload("/guild/join_approval", "guild-leader", {
            "guild_id": guild_id, "member_id": member_id, "approval": 1,
        })
        self.assertNotIn("_server_events", approved)
        self.assertEqual(approved["guild_info"]["member_num"], 2)
        self.assertEqual(
            self.payload("/title/login", "guild-member")["user"]["guild"],
            1,
        )
        internal = self.payload("/guild_battle/top", "guild-member")
        self.assertEqual(internal["guild"]["guild_info"]["guild_id"], guild_id)
        self.assertEqual(internal["guild"]["guild_info"]["member_num"], 2)
        classes = {
            entry["user"]: entry["role"]
            for entry in internal["guild"]["member"]
        }
        self.assertEqual(classes[internal["user"]["open_id"]], 3)
        for path, fields in (
            ("/guild/edit_guild_info", {"guild_id": guild_id,
                                         "name": "NO PERMITIDO"}),
            ("/guild/break_guild", {"guild_id": guild_id}),
            ("/guild/command_member", {"guild_id": guild_id,
                                        "member_id": created["user"]["open_id"],
                                        "command": 0}),
        ):
            self.assertEqual(
                self.raw_payload(path, "guild-member", fields)["response"]["error_code"],
                403,
            )

        join_notice = self.payload(
            "/mypage/get_message_info", "guild-leader",
            {"message_type": 2},
        )["message_info"]
        self.assertEqual(len(join_notice), 1)
        self.assertIn("se ha unido al Guild.", join_notice[0]["message"])
        self.assertEqual(join_notice[0]["guild_id"], guild_id)
        self.assertEqual(
            self.payload("/mypage/get_message_info", "guild-leader", {
                "message_type": 1,
            })["message_info"],
            [],
        )
        self.assertEqual(
            self.payload("/mypage/get_message_info", "guild-leader", {
                "message_type": 3,
            })["message_info"],
            [],
        )

        sent = self.payload("/mypage/chat", "guild-leader", {
            "message_type": 2, "message": "MENSAJE DE GUILD",
        })
        self.assertEqual(sent["message_info"][0]["guild_id"], guild_id)
        member_chat = self.payload("/mypage/get_message_info", "guild-member", {
            "message_type": 2,
        })
        self.assertEqual(member_chat["message_info"][0]["message"],
                         "MENSAJE DE GUILD")
        outsider_chat = self.payload("/mypage/get_message_info", "guild-outsider", {
            "message_type": 2,
        })
        self.assertEqual(outsider_chat["message_info"], [])

        edited = self.payload("/guild/edit_guild_info", "guild-leader", {
            "guild_id": guild_id, "name": "METAL ELITE", "logo": 9,
            "profile": "Nuevo texto", "join_type": 0,
        })
        self.assertEqual(edited["guild_info"]["name"], "METAL ELITE")
        self.assertEqual(edited["guild_info"]["logo"], 9)

        expelled = self.payload("/guild/command_member", "guild-leader", {
            "guild_id": guild_id, "member_id": member_id, "command": 0,
        })
        self.assertEqual(expelled["guild_info"]["member_num"], 1)
        self.assertEqual(
            self.payload("/title/login", "guild-member")["user"]["guild"], 0
        )

        joined = self.payload("/guild/join_guild", "guild-member", {
            "guild_id": guild_id,
        })
        self.assertEqual(joined["guild_info"]["member_num"], 2)
        left = self.payload("/setting/withdrawal_guild", "guild-member")
        self.assertEqual(left["user"]["guild"], 0)
        self.assertEqual(left["guild"]["guild_info"]["guild_id"], 0)
        self.assertEqual(left["guild"]["member"], [])
        self.assertNotIn("guild_info", left)
        self.assertNotIn("my_guild_info", left)
        reopened_after_leave = self.payload(
            "/guild_battle/top", "guild-member"
        )
        self.assertEqual(reopened_after_leave["user"]["guild"], 0)
        self.assertEqual(
            reopened_after_leave["guild"]["guild_info"]["guild_id"], 0
        )
        self.assertEqual(reopened_after_leave["guild"]["member"], [])
        broken = self.payload("/guild/break_guild", "guild-leader", {
            "guild_id": guild_id,
        })
        self.assertEqual(broken["user"]["guild"], 0)
        self.assertEqual(broken["guild"]["guild_info"]["guild_id"], 0)
        self.assertEqual(broken["guild"]["guild_info"]["name"], "")
        self.assertEqual(broken["guild"]["guild_info"]["logo_id"], 0)
        self.assertEqual(broken["guild"]["member"], [])
        self.assertNotIn("guild_info", broken)
        self.assertNotIn("my_guild_info", broken)
        reopened_after_break = self.payload(
            "/guild_battle/top", "guild-leader"
        )
        self.assertEqual(reopened_after_break["user"]["guild"], 0)
        self.assertEqual(
            reopened_after_break["guild"]["guild_info"]["guild_id"], 0
        )
        self.assertEqual(reopened_after_break["guild"]["member"], [])
        self.assertFalse(self.isolated_data.joinpath("guilds.json").read_text(
            encoding="utf-8"
        ).find('"METAL ELITE"') >= 0)

    def test_guild_search_uses_client_name_id_and_offset_fields(self) -> None:
        alpha = self.payload("/guild/build_guild", "search-alpha", {
            "name": "ÁGUILAS DEL METAL",
        })["guild_info"]
        beta = self.payload("/guild/build_guild", "search-beta", {
            "name": "BETA FORCE",
        })["guild_info"]
        numeric = self.payload("/guild/build_guild", "search-numeric", {
            "name": "14",
        })["guild_info"]

        by_name = self.payload("/guild/search_guild", "search-viewer", {
            "guild_name": "águilas", "offset": 0,
        })
        self.assertEqual(by_name["num"], 1)
        self.assertEqual(by_name["guild"][0]["guild_id"], alpha["guild_id"])

        by_id = self.payload("/guild/search_guild", "search-viewer", {
            "guild_id": beta["guild_id"], "offset": 0,
        })
        self.assertEqual(by_id["num"], 1)
        self.assertEqual(by_id["guild"][0]["name"], "BETA FORCE")

        numeric_name = self.payload(
            "/guild/search_guild", "search-viewer", {
                "guild_name": "14", "offset": 0,
            },
        )
        self.assertEqual(
            [entry["guild_id"] for entry in numeric_name["guild"]],
            [numeric["guild_id"]],
        )

        missing_id = self.payload("/guild/search_guild", "search-viewer", {
            "guild_id": 4_294_967_295, "offset": 0,
        })
        self.assertEqual(missing_id["guild"], [])
        self.assertEqual(missing_id["num"], 0)

        second_page = self.payload("/guild/search_guild", "search-viewer", {
            "offset": 1,
        })
        self.assertEqual(
            [entry["guild_id"] for entry in second_page["guild"]],
            [beta["guild_id"], numeric["guild_id"]],
        )

    def test_guild_kind_ids_roles_and_one_time_are_not_conflated(self) -> None:
        first = self.payload("/guild/build_guild", "guild-first", {
            "name": "FIRST",
        })
        self.assertEqual(first["guild_info"]["guild_id"], 1)
        self.payload("/guild/break_guild", "guild-first")

        second = self.payload("/guild/build_guild", "guild-owner", {
            "name": "SECOND", "join_type": 0,
        })
        guild_id = second["guild_info"]["guild_id"]
        self.assertEqual(guild_id, 2)
        # GuildKind sigue siendo NORMAL incluso cuando el ID real vale 2.
        self.assertEqual(second["user"]["guild"], 1)
        top = self.payload("/guild_battle/top", "guild-owner")
        self.assertEqual(top["user"]["guild"], 1)
        self.assertEqual(top["guild"]["guild_info"]["guild_id"], 2)

        self.assertNotIn("applicant_guild_info", top["guild"])
        self.assertNotIn("applicant_guild_info", top)

        joined = self.payload("/guild/join_guild", "guild-member", {
            "guild_id": guild_id,
        })
        self.assertEqual(joined["user"]["guild"], 1)
        member_id = joined["user"]["open_id"]
        transferred = self.payload("/guild/command_member", "guild-owner", {
            "guild_id": guild_id, "member_id": member_id, "command": 1,
        })
        roles = {entry["user"]: entry["role"]
                 for entry in transferred["guild"]["member"]}
        self.assertEqual(roles[member_id], 1)
        self.assertEqual(roles[second["user"]["open_id"]], 3)
        self.assertEqual(
            self.raw_payload("/guild/edit_guild_info", "guild-owner", {
                "guild_id": guild_id, "name": "FORBIDDEN",
            })["response"]["error_code"],
            403,
        )
        edited = self.payload("/guild/edit_guild_info", "guild-member", {
            "guild_id": guild_id, "name": "TRANSFERRED",
        })
        self.assertEqual(edited["guild_info"]["name"], "TRANSFERRED")
        left = self.payload("/setting/withdrawal_guild", "guild-owner")
        self.assertEqual(left["user"]["guild"], 0)

        one_time = self.raw_payload("/guild/join_onetime_guild", "outsider", {
            "guild_id": guild_id,
        })
        self.assertEqual(one_time["response"]["error_code"], 501)
        self.assertEqual(
            self.payload("/title/login", "outsider")["user"]["guild"], 0
        )
        dissolved = self.payload("/guild/break_guild", "guild-member")
        self.assertEqual(dissolved["user"]["guild"], 0)
        self.assertEqual(
            self.payload("/title/login", "guild-owner")["user"]["guild"], 0
        )

    def test_guild_top_does_not_duplicate_large_member_lists(self) -> None:
        terminals = [f"large-guild-{index}" for index in range(20)]
        clients = [self._resolve_client(value) for value in terminals]
        players = [public_player_id(value) for value in clients]
        members = [
            {"player_id": player, "client": client,
             "joined_at": "2026-08-30T00:00:00+00:00"}
            for player, client in zip(players, clients)
        ]
        state = {
            "version": 1, "next_id": 2,
            "guilds": {"1": {
                "guild_id": 1, "name": "FULL GUILD", "logo": 1,
                "profile": "", "language": 8, "play_type": 0,
                "join_type": 0, "open": 1, "limit_level": 1,
                "leader": players[0], "members": members,
                "requests": [], "camp_soldier": [{
                    "user": players[1], "unit_id": 1993,
                }], "gj": 0,
            }},
            "memberships": {player: 1 for player in players},
        }
        (self.isolated_data / "guilds.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        top = self.payload("/guild_battle/top", terminals[0])
        self.assertEqual(len(top["guild"]["member"]), 20)
        self.assertEqual(top["guild"]["guild_info"]["member_num"], 20)
        self.assertEqual(top["guild"]["my_soldier"], [])
        self.assertNotIn("guild_info", top)
        self.assertNotIn("my_guild_info", top)
        self.assertNotIn("member", top)
        self.assertLess(
            len(json.dumps(top["guild"], separators=(",", ":"))),
            10_000,
        )

    def test_guild_state_repairs_orphan_and_duplicate_references(self) -> None:
        created = self.payload("/guild/build_guild", "repair-owner", {
            "name": "REPAIR",
        })
        guild_id = created["guild_info"]["guild_id"]
        path = self.isolated_data / "guilds.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        owner_entry = state["guilds"][str(guild_id)]["members"][0]
        state["guilds"][str(guild_id)]["members"].append(dict(owner_entry))
        state["guilds"][str(guild_id)]["requests"] = [dict(owner_entry)]
        state["memberships"] = {"orphan-user": 999}
        path.write_text(json.dumps(state), encoding="utf-8")

        top = self.payload("/guild_battle/top", "repair-owner")
        self.assertEqual(top["user"]["guild"], 1)
        self.assertEqual(len(top["guild"]["member"]), 1)
        self.assertEqual(top["guild"]["applicant"], [])
        self.assertEqual(
            self.payload("/title/login", "unrelated-user")["user"]["guild"],
            0,
        )

    def test_guild_requests_and_mutations_are_isolated_by_guild_id(self) -> None:
        alpha = self.payload("/guild/build_guild", "alpha-owner", {
            "name": "ALPHA", "join_type": 1,
        })["guild_info"]["guild_id"]
        beta = self.payload("/guild/build_guild", "beta-owner", {
            "name": "BETA", "join_type": 1,
        })["guild_info"]["guild_id"]
        self.payload("/guild/request_enrollment", "applicant", {
            "guild_id": alpha,
        })
        self.payload("/guild/request_enrollment", "applicant", {
            "guild_id": beta,
        })
        self.assertEqual(
            self.payload("/guild_battle/top", "alpha-owner")["guild"]["applicant"],
            [],
        )
        beta_top = self.payload("/guild_battle/top", "beta-owner")
        self.assertEqual(len(beta_top["guild"]["applicant"]), 1)

        forbidden_gj = self.raw_payload("/guild/gj", "alpha-owner", {
            "guild_id": beta,
        })
        self.assertEqual(forbidden_gj["response"]["error_code"], 403)
        self.assertEqual(
            self.payload("/guild_battle/top", "beta-owner")["guild"]["gj"],
            0,
        )
        forbidden_leave = self.raw_payload(
            "/setting/withdrawal_guild", "alpha-owner", {"guild_id": beta}
        )
        self.assertEqual(forbidden_leave["response"]["error_code"], 403)
        self.assertEqual(
            self.payload("/title/login", "alpha-owner")["user"]["guild"], 1
        )

    def test_public_http_errors_do_not_disclose_internals(self) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/ruta-inexistente"
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        error = caught.exception
        self.assertEqual(error.code, 404)
        error_body = error.read()
        error.close()
        self.assertEqual(
            json.loads(error_body), {"response": {"error_code": 1}}
        )
        server_header = error.headers.get("Server", "")
        self.assertNotIn("MSA", server_header)
        self.assertNotIn("Python", server_header)

    def test_profile_and_deck_are_isolated(self) -> None:
        changed = self.payload("/setting/change_setting", "device-a", {
            "name": "ALPHA", "profile": "Perfil A", "avatar": 17,
        })
        self.assertEqual(changed["user"]["name"], "ALPHA")
        self.assertNotEqual(
            self.payload("/setting/top", "device-b")["user"]["name"],
            "ALPHA",
        )
        self.payload("/build_up/set_deck", "device-a", {
            "deck_unit_1[]": [1989, 1969, 1923, -1],
            "deck_attribute_1[]": [0, 1, 2, 0],
            "cover_1": 2,
        })
        deck_a = self.payload("/build_up/top", "device-a")["user"]["deck"][0]
        deck_b = self.payload("/build_up/top", "device-b")["user"]["deck"][0]
        self.assertEqual(deck_a["unit_ids"][:3], [1989, 1969, 1923])
        self.assertEqual(deck_a["deck_attribute"][:3], [0, 1, 2])
        self.assertNotEqual(deck_a["unit_ids"][:3], deck_b["unit_ids"][:3])

    def test_online_1on1_supports_and_rotation(self) -> None:
        room_a = self.payload(
            "/online_1on1/create_or_enter_room", "device-a"
        )["room_id"]
        room_b = self.payload(
            "/online_1on1/create_or_enter_room", "device-b"
        )["room_id"]
        self.assertEqual(room_a, room_b)
        self.payload("/online_1on1/decide_room", "device-a", {
            "room_id": room_a, "deck_no": 1, "cover": 2,
            "supporter[]": [1989, 1969, 1923],
        })
        self.payload("/online_1on1/decide_room", "device-b", {
            "room_id": room_b, "deck_no": 1, "cover": 1,
            "supporter[]": [1889, 1874, 1849],
        })
        battle = self.payload("/online_1on1/battle_start", "device-a", {
            "room_id": room_a,
        })
        self.assertEqual([p["cover"] for p in battle["player_info"]], [2, 1])
        self.assertEqual(
            [u["unit_id"] for u in battle["player_info"][0]["supporter_param"]],
            [1989, 1969, 1923],
        )
        self.assertEqual(
            [u["unit_id"] for u in battle["player_info"][1]["supporter_param"]],
            [1889, 1874, 1849],
        )
        next_room = self.payload(
            "/online_1on1/create_or_enter_room", "device-a"
        )["room_id"]
        self.payload("/online_1on1/leave_room", "device-a", {"room_id": room_a})
        self.assertNotEqual(next_room, room_a)

    def test_friend_online_invitation_chat_and_battle_contracts(self) -> None:
        self.payload("/setting/change_setting", "friend-match-a", {
            "name": "CHALLENGER", "language": 3, "is_accept": 1,
        })
        self.payload("/setting/change_setting", "friend-match-b", {
            "name": "INVITED", "language": 3, "is_accept": 1,
        })
        self.payload("/friend/top", "friend-match-a")
        self.payload("/friend/top", "friend-match-b")
        invited = self.payload(
            "/friend/search", "friend-match-a", {"name": "INVITED"}
        )["friend"][0]["person"]["user"]
        self.payload(
            "/friend/relation", "friend-match-a", {"user_id": invited}
        )
        challenger = self.payload(
            "/friend/top", "friend-match-b"
        )["pending"][0]["person"]["user"]
        self.payload("/friend/relation", "friend-match-b", {
            "user_id": challenger, "is_accept": 1,
        })

        room_id = self.payload(
            "/friend_online_1on1/create_room", "friend-match-a", {}
        )["room_id"]
        decision = self.payload(
            "/friend_online_1on1/decide_room", "friend-match-a", {
                "room_id": room_id,
                "friend_open_id": invited,
                "deck_no": 1,
                "cover": 2,
            },
        )
        self.assertEqual(decision["type"], 1)

        private_chat = self.payload(
            "/mypage/get_message_info", "friend-match-b", {
                "message_type": 3, "open_id": challenger,
            },
        )["message_info"]
        invitation = next(
            message for message in private_chat
            if message["message"] == "FRIEND BATTLE"
        )
        self.assertEqual(
            json.loads(invitation["param"]),
            {"type": 8, "id": room_id},
        )
        self.assertEqual(invitation["person"]["user"], challenger)
        self.assertEqual(invitation["to_person"]["user"], invited)
        self.assertNotEqual(
            self.payload("/top_menu/top", "friend-match-b")
            ["last_message_date"],
            "2023-01-12 15:30:00",
        )
        world_chat = self.payload(
            "/mypage/get_message_info", "friend-match-b", {
                "message_type": 1,
            },
        )["message_info"]
        self.assertFalse(any(
            message.get("param") == invitation["param"]
            for message in world_chat
        ))

        accepted = self.payload(
            "/friend_online_1on1/decide_room", "friend-match-b", {
                "room_id": room_id,
                "open_id[]": [challenger],
                "deck_no": 1,
                "cover": 1,
            },
        )
        self.assertEqual(accepted["type"], 2)

        battle = self.payload(
            "/friend_online_1on1/battle_start", "friend-match-a", {
                "room_id": room_id,
            },
        )
        self.assertEqual(len(battle["player_info"]), 2)
        self.assertEqual(
            [entry["person"]["user"] for entry in battle["player_info"]],
            [challenger, invited],
        )
        self.assertEqual(
            [entry["person"]["name"] for entry in battle["player_info"]],
            ["CHALLENGER", "INVITED"],
        )
        invited_battle = self.payload(
            "/friend_online_1on1/battle_start", "friend-match-b", {
                "room_id": room_id,
            },
        )
        self.assertEqual(invited_battle["player_info"], battle["player_info"])
        self.payload(
            "/friend_online_1on1/battle_end", "friend-match-a", {
                "room_id": room_id, "battle_result": 1,
            },
        )
        self.payload(
            "/friend_online_1on1/battle_end", "friend-match-b", {
                "room_id": room_id, "battle_result": 2,
            },
        )

    def test_guild_online_2on2_waits_for_four_real_guild_members(self) -> None:
        members = [f"guild-2on2-{index}" for index in range(4)]
        names = ["LEADER", "ALLY ONE", "ALLY TWO", "ALLY THREE"]
        player_ids = []
        for client, name in zip(members, names):
            changed = self.payload("/setting/change_setting", client, {
                "name": name, "language": 3,
            })
            player_ids.append(changed["user"]["open_id"])
        created = self.payload("/guild/build_guild", members[0], {
            "name": "2ON2 TEAM", "logo": 7, "join_type": 0,
        })
        guild_id = created["guild_info"]["guild_id"]
        for client in members[1:]:
            joined = self.payload("/guild/join_guild", client, {
                "guild_id": guild_id,
            })
            self.assertEqual(joined["guild_info"]["guild_id"], guild_id)

        room_id = self.payload(
            "/guild_online_2on2/create_room", members[0], {}
        )["room_id"]
        guild_chat = self.payload(
            "/mypage/get_message_info", members[1], {"message_type": 2}
        )["message_info"]
        invitation = next(
            message for message in guild_chat
            if message.get("message") == "GUILD 2ON2 BATTLE"
        )
        self.assertEqual(invitation["message"], "GUILD 2ON2 BATTLE")
        self.assertEqual(
            json.loads(invitation["param"]),
            {"type": 4, "id": room_id},
        )
        self.assertEqual(invitation["guild_id"], guild_id)
        self.assertNotEqual(
            self.payload("/top_menu/top", members[1])
            ["last_message_date"],
            "2023-01-12 15:30:00",
        )

        server.GUILD_ONLINE.WAIT_SECONDS = 0.01
        waiting = self.raw_payload(
            "/guild_online_2on2/battle_start", members[0], {
                "room_id": room_id,
            },
        )
        self.assertEqual(waiting["response"]["error_code"], 408)
        self.assertEqual(waiting["joined"], 1)
        self.assertNotIn("player_info", waiting)

        for slot, client in enumerate(members, 1):
            decision = self.payload(
                "/guild_online_2on2/decide_room", client, {
                    "room_id": room_id, "deck_no": 1,
                    "cover": slot % 3,
                },
            )
            self.assertEqual(decision["type"], slot)

        battle = self.payload(
            "/guild_online_2on2/battle_start", members[0], {
                "room_id": room_id,
            },
        )
        self.assertEqual(len(battle["player_info"]), 4)
        self.assertEqual(
            [entry["person"]["user"] for entry in battle["player_info"]],
            player_ids,
        )
        self.assertEqual(
            [entry["person"]["name"] for entry in battle["player_info"]],
            names,
        )
        teammate_view = self.payload(
            "/guild_online_2on2/battle_start", members[3], {
                "room_id": room_id,
            },
        )
        self.assertEqual(teammate_view["player_info"], battle["player_info"])

        outsider = "guild-2on2-outsider"
        self.payload("/guild/build_guild", outsider, {
            "name": "OTHER TEAM", "join_type": 0,
        })
        rejected = self.raw_payload(
            "/guild_online_2on2/decide_room", outsider, {
                "room_id": room_id, "deck_no": 1,
            },
        )
        self.assertEqual(rejected["response"]["error_code"], 403)

    def test_four_concurrent_players_share_one_2on2_room(self) -> None:
        matchmaker = OnlineMatchmaker()
        barrier = threading.Barrier(5)
        rooms: list[str] = []
        rooms_lock = threading.Lock()

        def enter(index: int) -> None:
            barrier.wait()
            room = matchmaker.enter("2on2", f"p{index}")
            with rooms_lock:
                rooms.append(room["id"])

        threads = [threading.Thread(target=enter, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertEqual(len(rooms), 4)
        self.assertEqual(len(set(rooms)), 1)

    def test_social_friendship_chat_and_blacklist_are_persistent(self) -> None:
        self.payload("/setting/change_setting", "device-a", {
            "name": "ALPHA", "profile": "Perfil A", "language": 3,
            "is_accept": 1,
        })
        self.payload("/setting/change_setting", "device-b", {
            "name": "BETA", "profile": "Perfil B", "language": 3,
            "is_accept": 1,
        })
        self.payload("/friend/top", "device-a")
        self.payload("/friend/top", "device-b")

        search = self.payload("/friend/search", "device-a", {"name": "BETA"})
        self.assertEqual(len(search["friend"]), 1)
        beta_id = search["friend"][0]["person"]["user"]
        self.assertEqual(
            search["friend"][0]["person"]["detail"]["is_accept"], 1
        )
        self.payload("/friend/relation", "device-a", {"user_id": beta_id})

        pending = self.payload("/friend/top", "device-b")["pending"]
        self.assertEqual(len(pending), 1)
        alpha_id = pending[0]["person"]["user"]
        self.payload("/friend/relation", "device-b", {
            "user_id": alpha_id, "is_accept": 1,
        })
        self.assertEqual(
            self.payload("/friend/top", "device-a")["friend"][0]
            ["person"]["name"],
            "BETA",
        )
        self.assertEqual(
            self.payload("/friend/top", "device-b")["friend"][0]
            ["person"]["name"],
            "ALPHA",
        )

        self.payload("/mypage/chat", "device-a", {"message": "Hola BETA"})
        chat = self.payload("/mypage/get_message_info", "device-b", {
            "language_filter[]": [3], "message_type": 1,
        })
        self.assertEqual(chat["message_info"][0]["message"],
                         "Hola BETA")
        self.assertTrue(chat["last_message_date"])
        self.assertEqual(chat["message_info"][0]["text"],
                         "Hola BETA")
        self.assertIsInstance(
            chat["message_info"][0]["datetime"], int
        )
        self.assertTrue(chat["message_info"][0]["timestamp"].isdigit())
        self.assertEqual(
            chat["message_info"][0]["person"]["user"], alpha_id
        )
        self.assertEqual(chat["message_info"][0]["person"]["type"], 1)
        self.assertNotIn("to_person", chat["message_info"][0])
        own_chat = self.payload("/mypage/get_message_info", "device-a", {
            "message_type": "1",
        })
        own_message = next(
            message for message in own_chat["message_info"]
            if message["person"]["user"] == alpha_id
        )
        self.assertEqual(own_message["person"]["type"], 0)
        self.assertEqual(chat["chat_chance"], 10)
        self.payload("/mypage/chat", "device-a", {"message": "Más reciente"})
        refreshed_chat = self.payload(
            "/mypage/get_message_info", "device-b",
            {"language_filter[]": [3], "message_type": 1},
        )
        self.assertEqual(refreshed_chat["message_info"][0]["message"],
                         "Más reciente")

        self.payload("/mypage/add_black_list", "device-b", {
            "user_id": alpha_id,
        })
        blocked_chat = self.payload(
            "/mypage/get_message_info", "device-b",
            {"language_filter[]": [3], "message_type": 1},
        )
        self.assertEqual(blocked_chat["message_info"], [])
        self.assertEqual(
            self.payload("/setting/top", "device-b")["black_list"][0]["name"],
            "ALPHA",
        )
        self.payload("/setting/remove_black_list", "device-b", {
            "user_id": alpha_id,
        })
        self.assertEqual(
            self.payload("/setting/top", "device-b")["black_list"], []
        )

    def test_social_respects_request_preference_rejection_and_sever(self) -> None:
        self.payload("/setting/change_setting", "requester", {
            "name": "REQUESTER", "is_accept": 1,
        })
        self.payload("/setting/change_setting", "target", {
            "name": "TARGET", "is_accept": 0,
        })
        self.payload("/friend/top", "requester")
        self.payload("/friend/top", "target")
        search = self.payload(
            "/friend/search", "requester", {"name": "TARGET"}
        )
        target_id = search["friend"][0]["person"]["user"]
        self.assertEqual(
            search["friend"][0]["person"]["detail"]["is_accept"], 0
        )
        self.payload(
            "/friend/relation", "requester", {"user": target_id}
        )
        self.assertEqual(
            self.payload("/friend/top", "target")["pending"], []
        )

        self.payload("/setting/change_setting", "target", {"is_accept": 1})
        enabled = self.payload(
            "/friend/search", "requester", {"name": "TARGET"}
        )
        self.assertEqual(
            enabled["friend"][0]["person"]["detail"]["is_accept"], 1
        )
        self.payload(
            "/friend/relation", "requester", {"user": target_id}
        )
        sent_profile = self.payload(
            "/friend/search", "requester", {"name": "TARGET"}
        )["friend"][0]["person"]
        self.assertEqual(sent_profile["detail"]["friend_type"], 2)
        pending = self.payload("/friend/top", "target")["pending"]
        self.assertEqual(len(pending), 1)
        requester_id = pending[0]["person"]["user"]
        self.assertEqual(pending[0]["person"]["detail"]["friend_type"], 3)
        notices = self.payload(
            "/mypage/get_message_info", "target", {"message_type": 3}
        )["message_info"]
        self.assertEqual(notices[0]["message"], "FRIEND REQUEST")
        self.assertEqual(notices[0]["person"]["user"], requester_id)
        target_menu = self.payload("/top_menu/top", "target")
        requester_menu = self.payload("/top_menu/top", "requester")
        self.assertEqual(
            target_menu["last_message_date"], notices[0]["date_time"]
        )
        self.assertEqual(
            requester_menu["last_message_date"], "2023-01-12 15:30:00"
        )

        self.payload("/friend/sever", "target", {"user": requester_id})
        self.assertEqual(
            self.payload("/friend/top", "target")["pending"], []
        )

        self.payload(
            "/friend/relation", "requester", {"user": target_id}
        )
        self.payload("/friend/relation", "target", {"user": requester_id})
        self.assertEqual(
            self.payload("/friend/top", "target")["current_friend_num"], 1
        )
        self.payload("/friend/sever", "target", {"user": requester_id})
        self.assertEqual(
            self.payload("/friend/top", "target")["current_friend_num"], 0
        )
        self.assertEqual(
            self.payload("/friend/top", "requester")["current_friend_num"],
            0,
        )
        refreshed_notice = self.payload(
            "/mypage/get_message_info", "target", {"message_type": 3}
        )["message_info"][0]
        self.assertEqual(refreshed_notice["person"]["detail"]["friend_type"], 0)
        self.assertEqual(
            refreshed_notice["to_person"]["detail"]["friend_type"], 0
        )
        self.assertNotEqual(refreshed_notice["id"], notices[0]["id"])

        # Tras romper una amistad, cualquiera de los dos puede iniciar de
        # nuevo el flujo normal de solicitud.
        self.payload(
            "/friend/relation", "requester", {"user": target_id}
        )
        resent = self.payload("/friend/top", "target")["pending"]
        self.assertEqual(len(resent), 1)
        self.assertEqual(
            resent[0]["person"]["detail"]["friend_type"], 3
        )

        # El chat mundial también debe invalidar su fila al cambiar la
        # relación; es la ruta habitual para volver a abrir un perfil.
        self.payload("/mypage/chat", "requester", {
            "message_type": 1, "message": "perfil mundial",
        })
        world_pending = self.payload(
            "/mypage/get_message_info", "target", {"message_type": 1}
        )["message_info"][0]
        pending_world_id = world_pending["id"]
        self.payload("/friend/sever", "target", {"user": requester_id})
        world_clear = self.payload(
            "/mypage/get_message_info", "target", {"message_type": 1}
        )["message_info"][0]
        self.assertEqual(world_clear["person"]["detail"]["friend_type"], 0)
        self.assertNotEqual(world_clear["id"], pending_world_id)

    def test_private_messages_are_isolated_from_world_and_third_parties(self) -> None:
        for terminal, name in (
                ("sender", "SENDER"), ("recipient", "RECIPIENT"),
                ("observer", "OBSERVER")):
            self.payload("/setting/change_setting", terminal, {
                "name": name, "language": 3, "is_accept": 1,
            })
            self.payload("/friend/top", terminal)

        recipient_search = self.payload(
            "/friend/search", "sender", {"name": "RECIPIENT"}
        )
        recipient_id = recipient_search["friend"][0]["person"]["user"]
        sender_search = self.payload(
            "/friend/search", "recipient", {"name": "SENDER"}
        )
        sender_id = sender_search["friend"][0]["person"]["user"]

        self.payload("/mypage/chat", "sender", {
            "message_type": 3,
            "open_id": recipient_id,
            "message": "mensaje privado",
        })
        received = self.payload(
            "/mypage/get_message_info", "recipient",
            {"message_type": 3, "open_id": sender_id},
        )["message_info"]
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["message"], "mensaje privado")
        self.assertEqual(received[0]["person"]["user"], sender_id)
        self.assertEqual(received[0]["to_person"]["user"], recipient_id)

        observed = self.payload(
            "/mypage/get_message_info", "observer", {"message_type": 3}
        )
        self.assertEqual(observed["message_info"], [])
        world = self.payload(
            "/mypage/get_message_info", "recipient", {"message_type": 1}
        )
        self.assertEqual(world["message_info"], [])

    def test_ingame_mail_notifies_recipient_and_persists_read_state(self) -> None:
        sender = "notification-sender"
        recipient = "notification-recipient"
        observer = "notification-observer"
        for terminal in (sender, recipient, observer):
            self.payload("/friend/top", terminal)

        recipient_id = self.payload(
            "/top_menu/top", recipient
        )["user"]["open_id"]
        self.payload("/mypage/chat", sender, {
            "message_type": 3,
            "open_id": recipient_id,
            "message": "Tu sala ya esta lista",
        })

        self.assertEqual(
            self.payload("/top_menu/top", sender)["top_menu"]["unread_mail"],
            0,
        )
        self.assertEqual(
            self.payload("/top_menu/top", observer)["top_menu"]["unread_mail"],
            0,
        )
        recipient_menu = self.payload("/top_menu/top", recipient)
        self.assertEqual(recipient_menu["top_menu"]["unread_mail"], 1)

        mailbox = self.payload("/mail/top", recipient)["mail_box"]
        self.assertEqual(len(mailbox), 1)
        self.assertEqual(mailbox[0]["subject"], "Nuevo mensaje privado")
        self.assertEqual(mailbox[0]["body"], "Tu sala ya esta lista")
        self.assertEqual(mailbox[0]["unread"], 1)
        self.assertEqual(mailbox[0]["present_id"], 0)

        # Abrir el buzon confirma la lectura; la entrada se conserva para
        # consulta, pero el indicador del menu se apaga.
        self.assertEqual(
            self.payload("/top_menu/top", recipient)["top_menu"]["unread_mail"],
            0,
        )
        reopened = self.payload("/mail/top", recipient)["mail_box"]
        self.assertEqual(reopened[0]["unread"], 0)
        empty_present = self.payload("/mail/get_present", recipient)
        self.assertEqual(empty_present["item"], [])

        self.payload("/setting/change_setting", recipient, {"is_accept": 1})
        self.payload("/friend/top", recipient)
        self.payload("/friend/relation", sender, {"user": recipient_id})
        self.assertEqual(
            self.payload("/top_menu/top", recipient)["top_menu"]["unread_mail"],
            1,
        )
        request_mail = self.payload("/mail/top", recipient)["mail_box"]
        friend_request = next(
            entry for entry in request_mail
            if entry["subject"] == "Nueva solicitud de amistad"
        )
        self.assertEqual(friend_request["body"], "FRIEND REQUEST")
        self.assertEqual(friend_request["unread"], 1)
        self.assertTrue(next(
            self.isolated_data.glob("clients/*/ingame_notifications.json")
        ).is_file())

    def test_concurrent_social_chat_does_not_lose_messages(self) -> None:
        terminals = [f"chat-device-{index}" for index in range(8)]
        for index, terminal in enumerate(terminals):
            self.payload("/setting/change_setting", terminal, {
                "name": f"CHAT {index}", "language": 3,
            })
            self.payload("/friend/top", terminal)

        barrier = threading.Barrier(len(terminals) + 1)
        errors: list[Exception] = []

        def send(index: int, terminal: str) -> None:
            try:
                barrier.wait()
                self.payload("/mypage/chat", terminal, {
                    "message": f"mensaje-{index}",
                })
            except Exception as exc:  # pragma: no cover - diagnóstico del hilo
                errors.append(exc)

        threads = [
            threading.Thread(target=send, args=(index, terminal))
            for index, terminal in enumerate(terminals)
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])

        messages = self.payload(
            "/mypage/get_message_info", terminals[0],
            {"language_filter[]": [3], "message_type": 1},
        )["message_info"]
        self.assertEqual(
            {entry["message"] for entry in messages},
            {f"mensaje-{index}" for index in range(len(terminals))},
        )

    def test_chat_message_history_caps_at_fifty_and_evicts_oldest(self) -> None:
        self.payload("/setting/change_setting", "chat-user", {
            "name": "CHATTY", "language": 3, "is_accept": 1,
        })
        self.payload("/friend/top", "chat-user")

        # Enviar 55 mensajes secuenciales
        for i in range(1, 56):
            self.payload("/mypage/chat", "chat-user", {
                "message": f"mensaje-{i}", "message_type": 1,
            })

        chat = self.payload("/mypage/get_message_info", "chat-user", {
            "language_filter[]": [3], "message_type": 1,
        })
        message_list = chat["message_info"]
        self.assertEqual(len(message_list), 50)

        # El cliente espera los más recientes primero: del 55 al 6
        texts = [m["message"] for m in message_list]
        expected_texts = [f"mensaje-{i}" for i in range(55, 5, -1)]
        self.assertEqual(texts, expected_texts)

        # Verificar que los mensajes del 1 al 5 fueron descartados (FIFO)
        for i in range(1, 6):
            self.assertNotIn(f"mensaje-{i}", texts)

        # Mundo vive en su propio almacén; social.json ya no contiene Chat.
        social_state = json.loads(
            (self.isolated_data / "social.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("messages", social_state)
        world_state = json.loads(
            (self.isolated_data / "chat_world.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(world_state["messages"]), 50)
        self.assertEqual(world_state["messages"][0]["message"], "mensaje-6")
        self.assertEqual(world_state["messages"][-1]["message"], "mensaje-55")

        chat_log = (self.isolated_data / "ChatLog.log").read_text(encoding="utf-8")
        self.assertIn(": mensaje-55\n", chat_log)
        self.assertNotIn(": mensaje-1\n", chat_log)
        self.assertNotIn(": mensaje-5\n", chat_log)
        self.assertIn(": mensaje-6\n", chat_log)

        # Una notificación privada no desaloja ningún mensaje mundial.
        self.payload("/setting/change_setting", "target-user", {
            "name": "TARGET", "language": 3, "is_accept": 1,
        })
        self.payload("/friend/top", "target-user")
        target_id = self.payload(
            "/friend/search", "chat-user", {"name": "TARGET"}
        )["friend"][0]["person"]["user"]
        self.payload("/friend/relation", "chat-user", {"user_id": target_id})

        world_after_req = json.loads(
            (self.isolated_data / "chat_world.json").read_text(encoding="utf-8")
        )
        private_after_req = json.loads(
            (self.isolated_data / "chat_private.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(world_after_req["messages"]), 50)
        self.assertEqual(world_after_req["messages"][0]["message"], "mensaje-6")
        self.assertEqual(world_after_req["messages"][-1]["message"], "mensaje-55")
        self.assertEqual(len(private_after_req["messages"]), 1)
        self.assertEqual(private_after_req["messages"][0]["message"],
                         "FRIEND REQUEST")

        # Privados tienen su propio FIFO de 50 y no alteran Mundo.
        for i in range(1, 56):
            self.payload("/mypage/chat", "chat-user", {
                "message": f"privado-{i}", "message_type": 3,
                "open_id": target_id,
            })
        private_chat = self.payload(
            "/mypage/get_message_info", "target-user",
            {"message_type": 3},
        )["message_info"]
        self.assertEqual(len(private_chat), 50)
        self.assertEqual(
            [entry["message"] for entry in private_chat],
            [f"privado-{i}" for i in range(55, 5, -1)],
        )
        private_state = json.loads(
            (self.isolated_data / "chat_private.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(private_state["messages"]), 50)
        self.assertEqual(private_state["messages"][0]["message"], "privado-6")

    def test_each_guild_chat_has_an_independent_fifty_message_fifo(self) -> None:
        alpha = self.payload("/guild/build_guild", "guild-chat-alpha", {
            "name": "CHAT ALPHA",
        })
        beta = self.payload("/guild/build_guild", "guild-chat-beta", {
            "name": "CHAT BETA",
        })
        alpha_id = alpha["guild_info"]["guild_id"]
        beta_id = beta["guild_info"]["guild_id"]
        alpha_user = alpha["user"]["open_id"]
        beta_user = beta["user"]["open_id"]
        self.payload("/friend/top", "guild-chat-alpha")
        self.payload("/friend/top", "guild-chat-beta")

        def messages(prefix: str, sender: str, guild_id: int) -> list[dict]:
            return [{
                "id": f"{prefix}-{index}", "sender": sender,
                "message": f"{prefix}-{index}", "message_type": 2,
                "recipient": "", "guild_id": guild_id, "timestamp": index,
                "datetime": f"2026-01-01 00:00:{index % 60:02d}",
            } for index in range(1, 56)]

        (self.isolated_data / "chat_guild.json").write_text(json.dumps({
            "version": 1,
            "guilds": {
                str(alpha_id): messages("alpha", alpha_user, alpha_id),
                str(beta_id): messages("beta", beta_user, beta_id),
            },
        }), encoding="utf-8")
        alpha_chat = self.payload(
            "/mypage/get_message_info", "guild-chat-alpha",
            {"message_type": 2},
        )["message_info"]
        beta_chat = self.payload(
            "/mypage/get_message_info", "guild-chat-beta",
            {"message_type": 2},
        )["message_info"]
        self.assertEqual(len(alpha_chat), 50)
        self.assertEqual(len(beta_chat), 50)
        self.assertEqual(alpha_chat[0]["message"], "alpha-55")
        self.assertEqual(alpha_chat[-1]["message"], "alpha-6")
        self.assertEqual(beta_chat[0]["message"], "beta-55")
        self.assertEqual(beta_chat[-1]["message"], "beta-6")
        persisted = json.loads((
            self.isolated_data / "chat_guild.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(len(persisted["guilds"][str(alpha_id)]), 50)
        self.assertEqual(len(persisted["guilds"][str(beta_id)]), 50)

    def test_legacy_combined_chat_is_partitioned_without_cross_eviction(self) -> None:
        legacy = {
            "version": 1, "players": {}, "friends": {}, "requests": {},
            "blacklists": {},
            "messages": [
                {"id": "w", "sender": "w", "message": "WORLD",
                 "message_type": 1, "timestamp": 1},
                {"id": "p", "sender": "p", "recipient": "r",
                 "message": "PRIVATE", "message_type": 3, "timestamp": 2},
                {"id": "g", "sender": "g", "guild_id": 9,
                 "message": "GUILD", "message_type": 2, "timestamp": 3},
            ],
        }
        (self.isolated_data / "social.json").write_text(
            json.dumps(legacy), encoding="utf-8"
        )
        self.payload("/friend/top", "migration-user")
        social = json.loads((self.isolated_data / "social.json").read_text(
            encoding="utf-8"))
        world = json.loads((self.isolated_data / "chat_world.json").read_text(
            encoding="utf-8"))
        private = json.loads((self.isolated_data / "chat_private.json").read_text(
            encoding="utf-8"))
        guild = json.loads((self.isolated_data / "chat_guild.json").read_text(
            encoding="utf-8"))
        self.assertNotIn("messages", social)
        self.assertEqual([m["message"] for m in world["messages"]], ["WORLD"])
        self.assertEqual([m["message"] for m in private["messages"]], ["PRIVATE"])
        self.assertEqual([m["message"] for m in guild["guilds"]["9"]],
                         ["GUILD"])

    def test_user_connection_and_disconnection_tracking(self) -> None:
        # Configurar perfil y realizar solicitud inicial
        self.payload("/setting/change_setting", "tracker-user", {
            "name": "TEST_NICK", "language": 3,
        })
        self.payload("/top_menu/top", "tracker-user")

        # Verificar que se registró la conexión
        log_content = self.test_user_log.read_text(encoding="utf-8")
        self.assertIn("Registro de Usuarios de Metal Slug Attack", log_content)
        self.assertIn("## Batallas PvP Activas", log_content)
        self.assertIn("## Historial de Conexiones y Eventos", log_content)
        self.assertIn("IP: 127.0.0.1", log_content)
        expected_player = public_player_id(self._resolve_client("tracker-user"))
        self.assertIn("Cliente: " + expected_player, log_content)
        self.assertNotIn("Cliente: " + self._resolve_client("tracker-user"), log_content)
        self.assertIn("Nickname: TEST_NICK", log_content)
        self.assertIn("CONEXION", log_content)

        # Actualizar nickname
        self.payload("/setting/change_setting", "tracker-user", {
            "name": "RENAMED_NICK",
        })

        # Forzar chequeo de timeout simulando paso del tiempo
        for session in server.USER_TRACKER._sessions.values():
            session.last_seen -= 10.0  # envejecer la sesión

        timed_out = server.USER_TRACKER.check_timeouts()
        self.assertEqual(len(timed_out), 1)

        # Verificar que se registró la desconexión con el nickname actualizado y el ID de cliente
        log_content = self.test_user_log.read_text(encoding="utf-8")
        self.assertIn("Nickname: RENAMED_NICK", log_content)
        self.assertIn("Cliente: " + expected_player, log_content)
        self.assertIn("DESCONEXION", log_content)

        # Reingreso tras desconexión registra nueva conexión
        self.payload("/top_menu/top", "tracker-user")
        log_lines = [line for line in self.test_user_log.read_text(encoding="utf-8").splitlines() if "RENAMED_NICK" in line]
        self.assertTrue(len(log_lines) >= 2)
        self.assertIn("CONEXION", log_lines[-1])
        self.assertIn("Cliente: " + expected_player, log_lines[-1])

    def test_user_pvp_battle_tracking_in_log(self) -> None:
        self.payload("/setting/change_setting", "pvp-user-1", {"name": "P1_HERO"})
        self.payload("/setting/change_setting", "pvp-user-2", {"name": "P2_RIVAL"})

        room_1 = self.payload("/online_1on1/create_or_enter_room", "pvp-user-1")["room_id"]
        room_2 = self.payload("/online_1on1/create_or_enter_room", "pvp-user-2")["room_id"]
        self.assertEqual(room_1, room_2)

        self.payload("/online_1on1/decide_room", "pvp-user-1", {"room_id": room_1, "deck_no": 1})
        self.payload("/online_1on1/decide_room", "pvp-user-2", {"room_id": room_2, "deck_no": 1})

        # Iniciar batalla
        self.payload("/online_1on1/battle_start", "pvp-user-1", {"room_id": room_1})

        # Verificar sección activa en el log
        log_content = self.test_user_log.read_text(encoding="utf-8")
        self.assertIn("## Batallas PvP Activas", log_content)
        self.assertIn(f"**[1ON1] Sala `{room_1}`**", log_content)
        self.assertIn("Nickname: P1_HERO", log_content)
        self.assertIn("Nickname: P2_RIVAL", log_content)
        self.assertIn("BATALLA PVP 1ON1 (Inicio", log_content)

        # Finalizar batalla
        self.payload("/online_1on1/battle_end", "pvp-user-1", {"room_id": room_1})
        self.payload("/online_1on1/battle_end", "pvp-user-2", {"room_id": room_2})

        # Verificar que la batalla ya no está activa
        log_after = self.test_user_log.read_text(encoding="utf-8")
        self.assertIn("- Ninguna batalla en curso.", log_after)
        self.assertIn("BATALLA PVP 1ON1 (Fin", log_after)

    def test_nickname_change_tracking_in_log(self) -> None:
        # Primer ajuste de nombre
        self.payload("/setting/change_setting", "nick-user", {
            "name": "ORIGINAL_NICK", "language": 3,
        })

        # Cambiar el nickname
        self.payload("/setting/change_setting", "nick-user", {
            "name": "NEW_NICK_V1",
        })

        nick_log_content = self.test_nick_log.read_text(encoding="utf-8")
        self.assertIn("Registro de Cambios de Nickname", nick_log_content)
        self.assertIn("IP: 127.0.0.1", nick_log_content)
        self.assertIn("Nickname Anterior: ORIGINAL_NICK", nick_log_content)
        self.assertIn("Nickname Nuevo: NEW_NICK_V1", nick_log_content)

        # Modificar otro ajuste (avatar) sin cambiar nombre -> no debe duplicar log en Nicknames.log
        lines_before = len(self.test_nick_log.read_text(encoding="utf-8").splitlines())
        self.payload("/setting/change_setting", "nick-user", {
            "avatar": 20,
        })
        lines_after = len(self.test_nick_log.read_text(encoding="utf-8").splitlines())
        self.assertEqual(lines_before, lines_after)

        # Cambiar a un tercer nombre
        self.payload("/setting/change_setting", "nick-user", {
            "name": "FINAL_NICK_V2",
        })
        nick_log_updated = self.test_nick_log.read_text(encoding="utf-8")
        self.assertIn("Nickname Anterior: NEW_NICK_V1 | Nickname Nuevo: FINAL_NICK_V2", nick_log_updated)

        # Verificar que UserLog.log NO registra el cambio de nickname;
        # ese evento se mantiene exclusivamente en Nicknames.log.
        user_log_content = self.test_user_log.read_text(encoding="utf-8")
        self.assertNotIn("CAMBIO DE NICKNAME", user_log_content)

    def test_battle_tactics_full_lifecycle_and_persistence(self) -> None:
        terminal_a = "device-tactics-a"
        terminal_b = "device-tactics-b"

        # Los hashes fijan los artefactos verificados que habilitan la
        # temporada 23. La suite del servidor no necesita descifrar maestros
        # ni importar herramientas de parcheo del cliente.
        catalog = json.loads(
            (ROOT / "server_content/file_list.json").read_text(encoding="utf-8")
        )
        by_table = {
            urllib.parse.parse_qs(
                urllib.parse.urlsplit(entry["url"]).query
            )["table[]"][0]: entry
            for entry in catalog
        }
        for table in (
            "m_battle_tactics_season", "m_battle_tactics_unit",
            "m_schedule", "m_battle_tactics_setting",
        ):
            expected = MASTER_TABLE_EXPECTATIONS[table]
            self.assertEqual(
                {key: by_table[table][key] for key in expected}, expected
            )

        # 1. /battle_tactics/top
        top_a = self.payload("/battle_tactics/top", terminal_a)
        self.assertIn("user", top_a)
        self.assertIn("battle_tactics", top_a)
        tactics_a = top_a["battle_tactics"]
        self.assertEqual(tactics_a["season_no"], 23)
        self.assertEqual(tactics_a["win"], 0)
        self.assertEqual(tactics_a["battle_num"], 0)
        self.assertEqual(len(tactics_a["deck"]["unit_ids"]), 10)
        self.assertTrue(all(
            TACTICS_UNIT_MIN <= unit_id <= TACTICS_UNIT_MAX
            for unit_id in tactics_a["deck"]["unit_ids"]
        ))
        self.assertNotIn("supporter", tactics_a["deck"])
        self.assertEqual(len(tactics_a["supporter"]), TACTICS_SUPPORTER_COUNT)
        self.assertEqual(len(tactics_a["shop"]["contents"]), 28)
        self.assertEqual(tactics_a["shop"]["contents"][0]["chance"], 99)
        self.assertEqual(tactics_a["shop"]["contents"][13]["chance"], 1)

        # 2. Draft lifecycle: start_draft y end_draft
        draft_start = self.payload("/battle_tactics/start_draft", terminal_a, {"season_no": "23"})
        self.assertIn("seed", draft_start)
        self.assertEqual(draft_start["battle_tactics"]["is_draft"], 0)

        custom_units = [958, 959, 960, 961, 962, 963, 970, 971, 972, 973]
        custom_supporters = [1889, 1874]
        draft_end = self.payload("/battle_tactics/end_draft", terminal_a, {
            "season_no": "23",
            "deck_unit[]": custom_units,
            "deck_supporter[]": custom_supporters,
            "context": "test-context",
        })
        self.assertEqual(draft_end["battle_tactics"]["is_draft"], 1)
        self.assertEqual(draft_end["battle_tactics"]["deck"]["unit_ids"], custom_units)
        self.assertEqual(draft_end["battle_tactics"]["supporter"], custom_supporters)

        # 3. set_deck
        custom_attrs = [0, 1, 2, 0, 0, 0, 0, 0, 0, 0]
        set_deck = self.payload("/battle_tactics/set_deck", terminal_a, {
            "deck_unit[]": custom_units,
            "deck_attribute[]": custom_attrs,
            "deck_supporter[]": custom_supporters,
            "cover": 2,
        })
        self.assertEqual(set_deck["battle_tactics"]["deck"]["cover"], 2)
        self.assertEqual(set_deck["battle_tactics"]["deck"]["deck_attribute"], custom_attrs)
        self.assertEqual(set_deck["battle_tactics"]["supporter"], custom_supporters)

        # 4. Room, matchmaking and battle
        room = self.payload("/battle_tactics/create_or_enter_room", terminal_a, {"season_no": "23"})
        self.assertTrue(room["room_id"].startswith("msa-tactics-"))
        rival_room = self.payload(
            "/battle_tactics/create_or_enter_room", terminal_b,
            {"season_no": "23"},
        )
        self.assertEqual(rival_room["room_id"], room["room_id"])

        decide = self.payload("/battle_tactics/decide_room", terminal_a, {"room_id": room["room_id"], "cover": 2})
        self.assertEqual(decide["type"], 1)
        rival_decide = self.payload(
            "/battle_tactics/decide_room", terminal_b,
            {"room_id": room["room_id"], "cover": 1},
        )
        self.assertEqual(rival_decide["type"], 2)

        start = self.payload("/battle_tactics/battle_start", terminal_a, {"room_id": room["room_id"]})
        self.assertEqual(len(start["player_info"]), 2)
        self.assertEqual(
            [entry["person"]["user"] for entry in start["player_info"]],
            [public_player_id(self._resolve_client(terminal_a)),
             public_player_id(self._resolve_client(terminal_b))],
        )
        self.assertEqual(start["player_info"][0]["cover"], 2)
        self.assertEqual(start["player_info"][1]["cover"], 1)
        self.assertEqual(len(start["player_info"][0]["unit_param"]), 10)
        self.assertEqual(len(start["player_info"][1]["unit_param"]), 10)
        self.assertNotIn(
            -1,
            [unit["unit_id"] for player in start["player_info"]
             for unit in player["unit_param"]],
        )
        self.assertEqual(start["player_info"][0]["deck_attribute"], custom_attrs)
        self.assertTrue(all(
            len(player["supporter_param"]) == TACTICS_SUPPORTER_COUNT
            for player in start["player_info"]
        ))
        self.assertEqual(
            [unit["unit_id"] for unit in start["player_info"][0]["supporter_param"]],
            custom_supporters,
        )
        self.assertEqual(
            [unit["unit_id"] for unit in start["player_info"][1]["supporter_param"]],
            [1989, 1969],
        )
        # Las unidades de Tactics no heredan Status Strengthening de la cuenta.
        self.assertTrue(all(
            unit["status_seed"] == [0] * 9
            for player in start["player_info"]
            for unit in player["unit_param"] + player["supporter_param"]
        ))
        # SceneUnitView::getUnitVersionMax devuelve 5 para unidades normales
        # y 3 para Supporters en el cliente 7.13.0.
        self.assertTrue(all(
            unit["version"] == 5
            for player in start["player_info"]
            for unit in player["unit_param"]
        ))
        self.assertTrue(all(
            unit["version"] == 3
            for player in start["player_info"]
            for unit in player["supporter_param"]
        ))
        self.assertTrue(all(
            unit["skill"] == [50, 50, 0, 0]
            for player in start["player_info"]
            for unit in player["supporter_param"]
        ))
        rival_start = self.payload(
            "/battle_tactics/battle_start", terminal_b,
            {"room_id": room["room_id"]},
        )
        self.assertEqual(rival_start["player_info"], start["player_info"])

        # 5. battle_end
        coins_before = get_battle_tactics(self._resolve_client(terminal_a))["coin"]
        end = self.payload("/battle_tactics/battle_end", terminal_a, {
            "room_id": room["room_id"],
            "battle_result": 1,
            "battle_log": "tactics-replay-1",
        })
        self.assertEqual(end["battle_tactics"]["win"], 1)
        self.assertEqual(end["battle_tactics"]["battle_num"], 1)
        self.assertEqual(len(end["reward"]), 1)
        self.assertEqual(end["reward"][0]["num"], 2000)
        self.assertEqual(end["time_reward"], [{"item_id": 4894, "num": 1500}])
        self.assertEqual(end["battle_tactics"]["coin"], coins_before + 1500)

        # Reintentar el mismo reporte HTTP no duplica victorias ni recompensas.
        duplicate_end = self.payload("/battle_tactics/battle_end", terminal_a, {
            "room_id": room["room_id"], "battle_result": 1,
            "battle_log": "tactics-replay-1",
        })
        self.assertEqual(duplicate_end["battle_tactics"]["battle_num"], 1)
        self.assertEqual(duplicate_end["battle_tactics"]["win"], 1)
        self.assertEqual(duplicate_end["reward"], [])
        self.assertEqual(duplicate_end["time_reward"], [])
        rival_end = self.payload("/battle_tactics/battle_end", terminal_b, {
            "room_id": room["room_id"], "battle_result": 2,
            "battle_log": "tactics-replay-1",
        })
        self.assertEqual(rival_end["battle_tactics"]["battle_num"], 1)
        self.assertEqual(rival_end["battle_tactics"]["win"], 0)
        self.assertEqual(rival_end["reward"], [{"item_id": 4855, "num": 1000}])

        # 6. Shop buy
        shop_coin_before = duplicate_end["battle_tactics"]["coin"]
        buy = self.payload("/battle_tactics/buy", terminal_a, {"id": 15})
        shop_item_15 = next(
            item for item in buy["battle_tactics"]["shop"]["contents"]
            if item["id"] == 15
        )
        self.assertEqual(shop_item_15["chance"], 0)
        self.assertEqual(
            buy["battle_tactics"]["coin"],
            shop_coin_before - TACTICS_SHOP[15][2],
        )
        sold_out = self.payload("/battle_tactics/buy", terminal_a, {"id": 15})
        self.assertEqual(sold_out["battle_tactics"]["coin"],
                         buy["battle_tactics"]["coin"])

        # 7. Auxiliares: history, ranking, leave_room
        history = self.payload(
            "/battle_tactics/battle_history", terminal_a
        )["online_battle_result"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["battle_log"], "tactics-replay-1")
        self.assertEqual(history[0]["result"], 1)
        self.assertEqual(
            set(history[0]),
            {"1p", "2p", "battle_log", "result", "battle_time"},
        )
        ranking = self.payload(
            "/battle_tactics/ranking_top", terminal_a
        )["ranking"]
        self.assertEqual(len(ranking), 1)
        self.assertEqual(ranking[0]["season_no"], 23)
        self.assertEqual(ranking[0]["win"], 1)
        self.assertEqual(ranking[0]["order"], 1)
        self.assertEqual(ranking[0]["record"][0]["win"], 1)
        self.assertEqual(len(ranking[0]["record"]), 2)
        self.assertEqual(ranking[0]["record"][1]["win"], 0)
        self.assertEqual(self.payload("/battle_tactics/leave_room", terminal_a)["response"]["error_code"], 0)

        # 8. Aislamiento por cliente
        top_b = self.payload("/battle_tactics/top", terminal_b)
        self.assertEqual(top_b["battle_tactics"]["win"], 0)
        self.assertEqual(top_b["battle_tactics"]["battle_num"], 1)
        self.assertNotEqual(top_b["battle_tactics"]["deck"]["unit_ids"], custom_units)

        # Los perfiles creados con el rango de Rank y los Supports anidados
        # se migran al contrato nativo de la temporada 23.
        client_b = self._resolve_client(terminal_b)
        legacy = top_b["battle_tactics"]
        legacy["deck"]["unit_ids"] = [16, 17, 18, 19, 27, 936, 3, 6, 7, 8]
        legacy["deck"]["supporter"] = [1889, 1874, 1849]
        legacy.pop("supporter", None)
        storage.client_data_file(client_b, "battle_tactics.json").write_text(
            json.dumps(legacy), encoding="utf-8",
        )
        migrated = get_battle_tactics(client_b)
        self.assertEqual(
            migrated["deck"]["unit_ids"],
            [939, 940, 941, 944, 945, 947, 949, 950, 955, 957],
        )
        self.assertNotIn("supporter", migrated["deck"])
        self.assertEqual(migrated["supporter"], custom_supporters)

    def test_event_info_get_news_and_server_broadcast(self) -> None:
        terminal = "device-news-test"
        # 1. Lectura inicial de noticias por defecto
        res = self.payload("/event_info/get_news", terminal)
        self.assertIn("news", res)
        news_list = res["news"]
        self.assertIsInstance(news_list, list)
        self.assertTrue(len(news_list) >= 1)
        first_item = news_list[0]
        self.assertEqual(set(first_item), {"id", "kind", "subject", "body", "img", "unread"})
        self.assertEqual(first_item["unread"], 1)

        # 2. Agregar un nuevo anuncio desde el servidor
        from msa_server.news import add_news_message
        created = add_news_message("Aviso Importante", "Mantenimiento finalizado con éxito.")
        self.assertEqual(created["subject"], "Aviso Importante")

        # 3. Validar que la nueva noticia es entregada inmediatamente a los clientes
        updated = self.payload("/event_info/get_news", terminal)
        self.assertEqual(updated["news"][0]["subject"], "Aviso Importante")
        self.assertEqual(updated["news"][0]["body"], "Mantenimiento finalizado con éxito.")

    def test_online_battle_record_history_cap_and_rank_points(self) -> None:
        terminal_a = "device-p1"
        terminal_b = "device-p2"

        # 1. Crear sala 1on1 y emparejar
        room = self.payload("/online_1on1/create_or_enter_room", terminal_a)["room_id"]
        self.payload("/online_1on1/create_or_enter_room", terminal_b)
        self.payload("/online_1on1/decide_room", terminal_a, {"room_id": room, "deck_no": 1})
        self.payload("/online_1on1/decide_room", terminal_b, {"room_id": room, "deck_no": 1})
        self.payload("/online_1on1/battle_start", terminal_a, {"room_id": room})

        # 2. Terminar batalla: Terminal A gana (battle_result = 1) con un battle_log que contiene estadísticas
        custom_log_1 = "stats_damage_dealt=150000;base_damage=50000;units_lost=2"
        end_a = self.payload("/online_1on1/battle_end", terminal_a, {
            "room_id": room,
            "battle_result": 1,
            "battle_log": custom_log_1,
        })
        self.assertEqual(end_a["add_online_score"], WIN_RANK_POINTS)
        self.assertEqual(end_a["online"]["1on1_win"], 1)
        self.assertEqual(end_a["online"]["1on1_lose"], 0)

        # 3. Comprobar que el historial de A contiene el registro con daño/estadísticas y formato de Person
        history_a = self.payload("/online_1on1/history", terminal_a)["online_battle_result"]
        self.assertEqual(len(history_a), 1)
        rec = history_a[0]
        self.assertEqual(rec["result"], 1)
        self.assertEqual(rec["battle_log"], custom_log_1)
        self.assertEqual(rec["1p"]["online_score"], WIN_RANK_POINTS)
        self.assertIn("detail", rec["1p"])
        self.assertIn("detail", rec["2p"])
        self.assertEqual(
            set(rec), {"1p", "2p", "battle_log", "result", "battle_time"}
        )

        # 4. Terminal B envía su propio fin de batalla como derrota
        end_b = self.payload("/online_1on1/battle_end", terminal_b, {
            "room_id": room,
            "battle_result": 2,
            "battle_log": custom_log_1,
        })
        self.assertEqual(end_b["add_online_score"], 0)
        self.assertEqual(end_b["online"]["1on1_win"], 0)
        self.assertEqual(end_b["online"]["1on1_lose"], 1)

        # Comprobar que el historial de B contiene su registro de derrota
        history_b = self.payload("/online_1on1/history", terminal_b)["online_battle_result"]
        self.assertEqual(len(history_b), 1)
        self.assertEqual(history_b[0]["result"], 2)
        self.assertEqual(history_b[0]["battle_log"], custom_log_1)

        # 5. Jugar 5 batallas más para verificar el límite FIFO estricto de máximo 5 registros
        for i in range(2, 7):
            r_id = self.payload("/online_1on1/create_or_enter_room", terminal_a)["room_id"]
            self.payload("/online_1on1/create_or_enter_room", terminal_b)
            self.payload("/online_1on1/decide_room", terminal_a, {"room_id": r_id, "deck_no": 1})
            self.payload("/online_1on1/decide_room", terminal_b, {"room_id": r_id, "deck_no": 1})
            self.payload("/online_1on1/battle_start", terminal_a, {"room_id": r_id})
            log_i = f"battle_log_index_{i}"
            self.payload("/online_1on1/battle_end", terminal_a, {
                "room_id": r_id,
                "battle_result": 1,
                "battle_log": log_i,
            })

        # Total 6 batallas: el historial debe tener exactamente 5 registros (el más viejo fue desalojado)
        history_a_capped = self.payload("/online_1on1/history", terminal_a)["online_battle_result"]
        self.assertEqual(len(history_a_capped), 5)
        self.assertEqual(history_a_capped[0]["battle_log"], "battle_log_index_6")
        self.assertEqual(history_a_capped[4]["battle_log"], "battle_log_index_2")
        # El puntaje reflejado en Record debe coincidir con las seis victorias.
        self.assertEqual(
            history_a_capped[0]["1p"]["online_score"],
            6 * WIN_RANK_POINTS,
        )

        # La seccion Ranking usa el contrato nativo y ordena por el puntaje
        # global persistido, no por una posicion fija local.
        online_ranking = self.payload(
            "/ranking/get_online_ranking", terminal_b
        )["ranking_data"]
        self.assertEqual(
            set(online_ranking), {"my_rank", "score", "top50ranking"}
        )
        self.assertEqual(online_ranking["my_rank"], 2)
        self.assertEqual(online_ranking["score"], 0)
        self.assertEqual(
            [entry["rank"] for entry in online_ranking["top50ranking"]],
            [1, 2],
        )
        self.assertEqual(
            online_ranking["top50ranking"][0]["person"]["online_score"],
            6 * WIN_RANK_POINTS,
        )
        self.assertEqual(
            online_ranking["top50ranking"][0]["person"]["user"],
            public_player_id(self._resolve_client(terminal_a)),
        )
        self.assertEqual(
            self.payload("/online_1on1/top", terminal_b)["online"]["my_rank"],
            2,
        )

    def test_global_rankings_compute_own_position_before_native_table_limits(self) -> None:
        terminal = "ranking-last-player"
        self.payload(
            "/setting/change_setting", terminal, {"name": "RANKING LAST"}
        )

        # Archivos sin player_snapshot reproducen las cuentas creadas antes
        # de que existieran las tablas globales.
        for index in range(1, 102):
            directory = storage.client_data_file(
                f"legacy-ranking-{index}", "profile.json"
            ).parent
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "profile.json").write_text(
                json.dumps({"name": f"GLOBAL {index}", "avatar": 1}),
                encoding="utf-8",
            )
            (directory / "online.json").write_text(
                json.dumps({"online_score": index}), encoding="utf-8"
            )
            (directory / "arena.json").write_text(
                json.dumps({"rating": index}), encoding="utf-8"
            )

        online = self.payload(
            "/ranking/get_online_ranking", terminal
        )["ranking_data"]
        self.assertEqual(online["my_rank"], 102)
        self.assertEqual(len(online["top50ranking"]), 50)
        self.assertEqual(online["top50ranking"][0]["rank"], 1)
        self.assertEqual(
            online["top50ranking"][0]["person"]["name"], "GLOBAL 101"
        )

        arena = self.payload("/arena/ranking_top", terminal)["ranking"]
        self.assertEqual(arena["order"], 102)
        self.assertEqual(len(arena["record"]), 100)
        self.assertEqual(arena["record"][0]["order"], 1)
        self.assertEqual(arena["record"][0]["rating"], 101)
        self.assertEqual(arena["record"][0]["person"]["name"], "GLOBAL 101")

    def test_rank_match_matchmaking_stars_promotions_and_demotions(self) -> None:
        terminal_a = "rank-player-a"
        terminal_b = "rank-player-b"
        self.payload("/setting/change_setting", terminal_a, {"name": "RANK ALPHA"})
        self.payload("/setting/change_setting", terminal_b, {"name": "RANK BRAVO"})
        # Reproduce el mazo real que causaba el cierre: diez casillas
        # persistidas, pero solo dos unidades equipadas.
        sparse_deck = [-1, 3, -1, -1, -1, -1, -1, -1, 6, -1]
        self.payload("/build_up/set_deck", terminal_b, {
            "deck_unit_2[]": sparse_deck,
            "deck_attribute_2[]": [0] * 10,
        })

        top = self.payload("/arena/top", terminal_a)
        self.assertEqual(
            {key: top["arena"][key] for key in ("season_no", "rank", "class", "win", "rating")},
            {"season_no": 58, "rank": 1, "class": 3, "win": 0, "rating": 0},
        )

        # Dos jugadores que buscan Rank Match reciben la misma sala Photon.
        room_a = self.payload("/arena/create_or_enter_room", terminal_a)["room_id"]
        room_b = self.payload("/arena/create_or_enter_room", terminal_b)["room_id"]
        self.assertEqual(room_a, room_b)
        self.assertTrue(room_a.startswith("msa-arena-"))
        self.payload("/arena/decide_room", terminal_a, {
            "room_id": room_a, "favorite_deck_no": 1, "under_cover": 2,
            "supporter[]": [1989, 1969, 1923],
        })
        self.payload("/arena/decide_room", terminal_b, {
            # Arena_DecideRoom 7.13.0 envia deck_no, no
            # favorite_deck_no. Debe conservar una eleccion distinta de 1.
            "room_id": room_a, "deck_no": 2,
        })
        battle = self.payload("/arena/battle_start", terminal_a, {"room_id": room_a})
        self.assertEqual(len(battle["player_info"]), 2)
        self.assertEqual(
            [entry["info"]["person"]["name"] for entry in battle["player_info"]],
            ["RANK ALPHA", "RANK BRAVO"],
        )
        for entry in battle["player_info"]:
            self.assertEqual(entry[""], entry["info"])
            self.assertNotIn("player_info", entry)
        rival = battle["player_info"][1]["info"]
        self.assertEqual(len(rival["unit_param"]), 10)
        self.assertEqual(
            [unit["unit_id"] for unit in rival["unit_param"]], sparse_deck
        )
        self.assertEqual(len(rival["deck_attribute"]), 10)
        self.assertEqual(battle["win_rating"], 10)
        self.assertEqual(battle["lose_rating"], -5)

        won = self.payload("/arena/battle_end", terminal_a, {
            "room_id": room_a, "battle_result": 1, "battle_log": "rank-replay-1",
        })
        lost = self.payload("/arena/battle_end", terminal_b, {
            "room_id": room_a, "battle_result": 2, "battle_log": "rank-replay-1",
        })
        self.assertEqual((won["arena"]["win"], won["arena"]["rating"]), (1, 10))
        self.assertEqual((lost["arena"]["win"], lost["arena"]["rating"]), (0, 0))
        self.assertEqual(won["add_rating"], 10)
        self.assertEqual(lost["add_rating"], -5)

        # Un reporte duplicado de battle_end no entrega estrellas nuevamente.
        duplicate = self.payload("/arena/battle_end", terminal_a, {
            "room_id": room_a, "battle_result": 1,
        })
        self.assertEqual((duplicate["arena"]["win"], duplicate["arena"]["rating"]), (1, 10))
        self.assertEqual(duplicate["add_rating"], 0)

        history = self.payload("/arena/battle_history", terminal_a)["online_battle_result"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["battle_log"], "rank-replay-1")
        self.assertEqual(
            set(history[0]),
            {"1p", "2p", "battle_log", "result", "battle_time"},
        )

        # La tercera estrella de Bronce III asciende a Bronce II.
        client_a = self._resolve_client(terminal_a)
        state_a = load_arena(client_a)
        state_a.update({"rank": 1, "class": 3, "win": 2, "rating": 20})
        save_arena(client_a, state_a)
        room2 = self.payload("/arena/create_or_enter_room", terminal_a)["room_id"]
        self.payload("/arena/create_or_enter_room", terminal_b)
        self.payload("/arena/battle_start", terminal_a, {"room_id": room2})
        promoted = self.payload("/arena/battle_end", terminal_a, {
            "room_id": room2, "battle_result": 1,
        })
        self.assertEqual(
            (promoted["arena"]["rank"], promoted["arena"]["class"], promoted["arena"]["win"]),
            (1, 2, 0),
        )
        self.assertTrue(promoted["reward_class"])

        # Perder sin estrellas desciende al nivel anterior con sus estrellas límite.
        state_a = load_arena(client_a)
        state_a.update({"rank": 1, "class": 2, "win": 0, "rating": 30})
        save_arena(client_a, state_a)
        room3 = self.payload("/arena/create_or_enter_room", terminal_a)["room_id"]
        self.payload("/arena/create_or_enter_room", terminal_b)
        self.payload("/arena/battle_start", terminal_a, {"room_id": room3})
        demoted = self.payload("/arena/battle_end", terminal_a, {
            "room_id": room3, "battle_result": 2,
        })
        self.assertEqual(
            (demoted["arena"]["rank"], demoted["arena"]["class"], demoted["arena"]["win"]),
            (1, 3, STARS_REQUIRED[1] - 1),
        )
        self.assertEqual(demoted["arena"]["rating"], 25)

        # La categoría alcanzada tiene piso en III: Plata III no vuelve a
        # Bronce I aunque continúen las derrotas sin estrellas.
        state_a = load_arena(client_a)
        state_a.update({"rank": 2, "class": 3, "win": 0, "rating": 30})
        save_arena(client_a, state_a)
        floor_room = self.payload("/arena/create_or_enter_room", terminal_a)["room_id"]
        self.payload("/arena/create_or_enter_room", terminal_b)
        self.payload("/arena/battle_start", terminal_a, {"room_id": floor_room})
        floor_loss = self.payload("/arena/battle_end", terminal_a, {
            "room_id": floor_room, "battle_result": 2,
        })
        self.assertEqual(
            (floor_loss["arena"]["rank"], floor_loss["arena"]["class"],
             floor_loss["arena"]["win"]),
            (2, 3, 0),
        )

        # Completar el nivel I asciende a III de la categoría siguiente.
        state_a = load_arena(client_a)
        state_a.update({"rank": 1, "class": 1,
                        "win": STARS_REQUIRED[1] - 1})
        save_arena(client_a, state_a)
        category_room = self.payload(
            "/arena/create_or_enter_room", terminal_a
        )["room_id"]
        self.payload("/arena/create_or_enter_room", terminal_b)
        self.payload("/arena/battle_start", terminal_a, {"room_id": category_room})
        category_up = self.payload("/arena/battle_end", terminal_a, {
            "room_id": category_room, "battle_result": 1,
        })
        self.assertEqual(
            (category_up["arena"]["rank"], category_up["arena"]["class"],
             category_up["arena"]["win"]),
            (2, 3, 0),
        )

        ranking = self.payload("/arena/ranking_top", terminal_a)["ranking"]
        self.assertGreaterEqual(len(ranking["record"]), 2)
        self.assertEqual(ranking["record"][0]["order"], 1)
        self.assertIn("person", ranking["record"][0])
        honor = self.payload("/arena/honor_history", terminal_a)["arena_honor_history"]
        self.assertIn("best_record", honor)

        # La escalera conserva los trece niveles definidos por las tablas maestras.
        self.assertEqual(len(ARENA_LADDER), 13)
        self.assertEqual(ARENA_LADDER[0], (1, 3))
        self.assertEqual(ARENA_LADDER[-1], (5, 0))

    def _resolve_client(self, terminal_id: str) -> str:
        from msa_server.identity import resolve_client_identity
        return resolve_client_identity({"terminal_id": terminal_id}, "127.0.0.1").client


if __name__ == "__main__":
    unittest.main()
