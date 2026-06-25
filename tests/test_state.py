import tempfile
import unittest
from pathlib import Path

from pnw.state import WatchState


class WatchStateTests(unittest.TestCase):
    def test_round_trip_nudge_counts_and_suppressed_sessions(self):
        state = WatchState()
        state.nudge_counts["session|recoverable_provider_failure"] = 8
        state.suppressed_sessions.add("session")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state.save(path)
            loaded = WatchState.load(path)
        self.assertEqual(8, loaded.nudge_counts["session|recoverable_provider_failure"])
        self.assertIn("session", loaded.suppressed_sessions)

    def test_nudge_key_includes_session_and_kind(self):
        state = WatchState()
        self.assertEqual("a.jsonl|recoverable_provider_failure", state.nudge_key(Path("a.jsonl"), "recoverable_provider_failure"))


if __name__ == "__main__":
    unittest.main()
