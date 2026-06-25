import subprocess
import sys
import unittest
from pathlib import Path

from pnw.classifier import Decision
from pnw.cli import _nudge_text_for_decision


ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / "watchdog.py"


class FixtureClassifierTests(unittest.TestCase):
    def run_fixture(self, name, harness, kind, allow):
        fixture = ROOT / "fixtures" / name
        proc = subprocess.run(
            [
                sys.executable,
                str(WATCHDOG),
                "test-fixture",
                str(fixture),
                "--harness",
                harness,
                "--expect-kind",
                kind,
                "--expect-allow",
                "true" if allow else "false",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_pi_proxy_timeout_allows_nudge(self):
        self.run_fixture("pi-proxy-timeout.jsonl", "pi", "recoverable_provider_failure", True)

    def test_pi_length_allows_nudge(self):
        self.run_fixture("pi-length.jsonl", "pi", "max_output_truncation", True)

    def test_pi_queued_continue_blocks_duplicate(self):
        self.run_fixture("pi-queued-continue.jsonl", "pi", "queued_nudge_exists", False)

    def test_pi_compaction_failure_blocks_blind_continue(self):
        self.run_fixture("pi-compaction-failure.jsonl", "pi", "context_or_compaction_failure", False)

    def test_omp_timeout_allows_nudge(self):
        self.run_fixture("omp-timeout.jsonl", "omp", "recoverable_provider_failure", True)

    def test_openclaude_api_error_allows_nudge(self):
        self.run_fixture("openclaude-api-error.jsonl", "openclaude", "recoverable_provider_failure", True)

    def test_generic_log_timeout_allows_nudge(self):
        self.run_fixture("generic-timeout.log", "generic", "recoverable_provider_failure", True)

    def test_recovered_timeout_does_not_nudge_again(self):
        self.run_fixture("pi-recovered-timeout.jsonl", "pi", "complete_or_idle", False)

    def test_new_user_turn_after_recovery_resets_old_failure(self):
        self.run_fixture("pi-new-user-after-recovery.jsonl", "pi", "awaiting_assistant", False)

    def test_tool_progress_after_timeout_does_not_nudge_old_failure(self):
        self.run_fixture("pi-tool-progress-after-timeout.jsonl", "pi", "active_tool_wait", False)

    def test_tool_result_after_timeout_does_not_nudge_old_failure(self):
        self.run_fixture("pi-tool-result-after-timeout.jsonl", "pi", "active_generation", False)

    def test_winerror_echo_fragments_do_not_clear_failure(self):
        self.run_fixture("pi-winerror-echo-fragments.jsonl", "pi", "recoverable_provider_failure", True)

    def test_malformed_tool_call_block_allows_nudge(self):
        self.run_fixture("pi-malformed-tool-call-block.jsonl", "pi", "malformed_tool_call_blocked", True)

    def test_malformed_tool_call_block_uses_specific_nudge(self):
        decision = Decision("malformed_tool_call_blocked", True, "test")
        text = _nudge_text_for_decision(decision, "continue")
        self.assertIn("supported tool", text)
        self.assertIn("same tool/error repeats", text)

    def test_default_nudge_includes_loop_guard(self):
        decision = Decision("recoverable_provider_failure", True, "test")
        text = _nudge_text_for_decision(decision, "continue")
        self.assertIn("stay on the current task", text)
        self.assertIn("same tool/error repeats", text)

    def test_custom_nudge_text_is_preserved(self):
        decision = Decision("malformed_tool_call_blocked", True, "test")
        self.assertEqual("keep going", _nudge_text_for_decision(decision, "keep going"))


if __name__ == "__main__":
    unittest.main()
