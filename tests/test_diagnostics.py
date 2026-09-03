import json
import tempfile
import unittest
from pathlib import Path

from msa_server.diagnostics import DiagnosticWriter


class DiagnosticWriterTests(unittest.TestCase):
    def test_writes_both_captures_and_drains_on_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = DiagnosticWriter(max_pending=4)
            writer.start()
            self.assertTrue(writer.submit(
                root / "global.json", root / "client" / "request.json",
                b'{"request":1}',
            ))
            writer.stop()

            self.assertEqual(json.loads((root / "global.json").read_text()),
                             {"request": 1})
            self.assertEqual(
                json.loads((root / "client" / "request.json").read_text()),
                {"request": 1},
            )
            self.assertEqual(writer.snapshot()["written"], 1)

    def test_full_queue_drops_capture_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = DiagnosticWriter(max_pending=1)
            self.assertTrue(writer.submit(root / "a", root / "b", b"one"))
            self.assertFalse(writer.submit(root / "c", root / "d", b"two"))
            self.assertEqual(writer.snapshot()["dropped"], 1)
            writer.start()
            writer.stop()


if __name__ == "__main__":
    unittest.main()
