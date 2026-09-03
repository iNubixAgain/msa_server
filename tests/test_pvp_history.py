from __future__ import annotations

import unittest

from msa_server.pvp_history import battle_record, normalise_history


class PvpHistoryContractTests(unittest.TestCase):
    def test_uses_native_number_p_keys(self) -> None:
        record = battle_record(
            ({"user": "msa-a"}, {"user": "msa-b"}),
            "replay", 1, "2026-09-02 10:45:03",
        )
        self.assertEqual(
            set(record), {"1p", "2p", "battle_log", "result", "battle_time"}
        )

    def test_migrates_already_persisted_user_keys(self) -> None:
        history = normalise_history([{
            "user1": {"user": "msa-a"},
            "user2": {"user": "msa-b"},
            "battle_log": "existing-replay",
            "result": 2,
            "battle_time": "2026-09-02 10:45:04",
        }])
        self.assertEqual(history[0]["1p"]["user"], "msa-a")
        self.assertEqual(history[0]["2p"]["user"], "msa-b")
        self.assertNotIn("user1", history[0])
        self.assertNotIn("user2", history[0])


if __name__ == "__main__":
    unittest.main()
