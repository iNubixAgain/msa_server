from __future__ import annotations

import unittest
from datetime import datetime, timezone

from msa_server.clock import jst_now_string, response_clock


class ServerClockTests(unittest.TestCase):
    def test_utc_is_converted_to_jst(self) -> None:
        instant = datetime(2026, 9, 1, 16, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(jst_now_string(instant), "2026-09-02 01:00:00")

    def test_response_uses_one_consistent_current_time(self) -> None:
        instant = datetime(2026, 9, 1, 16, 0, 1, tzinfo=timezone.utc)
        response = response_clock(instant)
        self.assertEqual(response["error_code"], 0)
        self.assertEqual(response["now_time"], "2026-09-02 01:00:01")
        self.assertEqual(response["server_time"], response["now_time"])


if __name__ == "__main__":
    unittest.main()
