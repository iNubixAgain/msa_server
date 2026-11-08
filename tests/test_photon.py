import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from msa_server.photon import PhotonSupervisor


class PhotonSupervisorTests(unittest.TestCase):
    def test_log_queue_writes_every_line_and_stops_on_sentinel(self) -> None:
        supervisor = PhotonSupervisor(Path("luxon.exe"), Path("."))
        supervisor.log_queue.put("linea uno")
        supervisor.log_queue.put("linea dos")
        supervisor.log_queue.put(None)

        with self.assertLogs(level="INFO") as captured:
            supervisor._write_output()

        output = "\n".join(captured.output)
        self.assertIn("[Photon] linea uno", output)
        self.assertIn("[Photon] linea dos", output)

    def test_packet_traces_are_filtered_before_the_log_queue(self) -> None:
        supervisor = PhotonSupervisor(
            Path("luxon.exe"), Path("."), log_packet_events=False
        )
        process = SimpleNamespace(stdout=StringIO(
            "[Peer 1] [info] RaiseEvent actor=1 code=1\n"
            "[Peer 2] [info] DispatchEvent code=1 sender=1\n"
            "[ServerManager] [warning] Tick time budget exhausted!\n"
        ))

        supervisor._capture_output(process)

        self.assertEqual(supervisor.output_lines, 3)
        self.assertEqual(supervisor.suppressed_packet_lines, 2)
        self.assertEqual(
            supervisor.log_queue.get_nowait(),
            "[ServerManager] [warning] Tick time budget exhausted!",
        )
        self.assertTrue(supervisor.log_queue.empty())
        metrics = supervisor.snapshot()
        self.assertFalse(metrics["packet_logging_enabled"])
        self.assertEqual(metrics["suppressed_packet_lines"], 2)

    def test_packet_traces_can_be_enabled_for_diagnostics(self) -> None:
        supervisor = PhotonSupervisor(
            Path("luxon.exe"), Path("."), log_packet_events=True
        )
        process = SimpleNamespace(stdout=StringIO(
            "[Peer 1] [info] RaiseEvent actor=1 code=1\n"
        ))

        supervisor._capture_output(process)

        self.assertEqual(supervisor.suppressed_packet_lines, 0)
        self.assertIn("RaiseEvent actor=1", supervisor.log_queue.get_nowait())


if __name__ == "__main__":
    unittest.main()
