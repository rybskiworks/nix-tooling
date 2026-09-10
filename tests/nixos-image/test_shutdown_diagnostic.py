"""Portable checks for the separate guest-requested shutdown diagnostic."""

from contextlib import ExitStack
import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

import shutdown_diagnostic as diagnostic
from smoke_contract import FALLBACK, verdict


def valid_record():
    return {"request": "guest-systemctl-no-block", "request_status": 0, "elapsed": 12,
            "runtime_exit": {"code": os.CLD_EXITED, "status": 0}, "flush_marker": True,
            "kernel_power_down": True, "logs_status": 0, "signals": [], "logs": ""}


class DiagnosticTests(unittest.TestCase):
    def test_diagnostic_is_not_a_normal_host_grace_pass(self):
        record = valid_record()
        diagnostic.validate_diagnostic(record)
        report = {"mode": "shutdown-diagnostic", "activation": True, "registration": True,
                  "root_trusted": True, "qa_trusted": False, "nobody_denied": True,
                  "cleanup": {"empty_store": True, "children_empty": True, "forced": False, "errors": []},
                  "builds": ["ordinary", "untrusted-overrides"], "daemon_restart": True,
                  "stops": [], "shutdown_diagnostic": record}
        self.assertEqual(verdict(report), "guest_poweroff_diagnostic_passed")
        for change in ({"mode": "full"}, {"stops": [record]}, {"persistent_store": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                verdict(dict(report, **change))

    def test_diagnostic_rejects_missing_or_contradictory_evidence(self):
        for change in ({"request_status": 1}, {"request": "host-stop"}, {"elapsed": 30},
                       {"elapsed": -1}, {"runtime_exit": None},
                       {"runtime_exit": {"code": os.CLD_KILLED, "status": 9}},
                       {"flush_marker": False}, {"kernel_power_down": False},
                       {"logs_status": 1}, {"signals": [{"pid": 123}]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                diagnostic.validate_diagnostic(dict(valid_record(), **change))
        for marker in (*FALLBACK, "Power off not available: System halted instead"):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "fallback"):
                diagnostic.validate_diagnostic(dict(valid_record(), logs=marker))

    def test_snapshot_command_is_valid_bash_and_metadata_only(self):
        script = diagnostic.process_snapshot({"coreutils": "/nix/store/example"})
        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("environ", script)
        self.assertNotIn("ps ", script)
        self.assertIn("read -r -d '' -n 512", script)
        self.assertIn('test "$count" -le 128', script)
        self.assertIn("SigBlk:*|SigIgn:*|SigCgt:*", script)

    def test_snapshot_failures_and_output_limits_are_explicit(self):
        for result in ((1, "", "error"), (0, "x" * (diagnostic.SNAPSHOT_BYTES + 1), "")):
            fixture = Mock(spec=["spec", "execute"])
            fixture.spec = {"systemd": "/systemd", "coreutils": "/coreutils"}
            fixture.execute.return_value = result
            with self.subTest(result=result[0]), self.assertRaises(ValueError):
                diagnostic.snapshot(fixture, {})

    def fixture(self):
        fixture = Mock()
        fixture.inspect_running_vmm.return_value = 123
        fixture.supervisor.pidfds = {123: 4}
        fixture.supervisor.signal_events = []
        fixture.spec = {"systemd": "/nix/store/systemd"}
        fixture.name = "nixos-test"
        fixture.deadline = 100
        fixture.command.return_value = (0, "", "")
        fixture.runtime_pids.return_value = []
        fixture.capture_logs.return_value = json.dumps({"s": "system", "d": "MARKER\nPowering off.\nreboot: Power down\n"})
        return fixture

    def mocks(self, stack):
        stack.enter_context(patch.object(diagnostic.os, "dup", return_value=5))
        close = stack.enter_context(patch.object(diagnostic.os, "close"))
        stack.enter_context(patch.object(diagnostic.os, "waitid", return_value=None))
        stack.enter_context(patch.object(diagnostic.time, "monotonic", side_effect=[10, 11, 12]))
        observer = stack.enter_context(patch.object(diagnostic, "observe_vmm_exit", return_value={"code": os.CLD_EXITED, "status": 0}))
        return observer, close

    def test_request_uses_exact_receiver_and_never_host_stop(self):
        fixture, record = self.fixture(), {}
        with ExitStack() as stack:
            observer, close = self.mocks(stack)
            diagnostic.observe_guest_poweroff(fixture, "MARKER", record)
        self.assertEqual(fixture.command.call_count, 1)
        args = fixture.command.call_args.args[0]
        self.assertEqual(args[-7:], ["/nix/store/systemd/bin/systemctl", "--system", "--no-block",
                                    "--no-ask-password", "--job-mode=replace-irreversibly", "start", "poweroff.target"])
        self.assertEqual(args[0], "exec")
        self.assertNotIn("stop", args)
        self.assertEqual(observer.call_args.args, (fixture, 123, 5, 10, 40))
        close.assert_called_once_with(5)
        self.assertTrue(record["kernel_power_down"])

    def test_request_error_still_collects_exit_and_logs_without_becoming_pass(self):
        fixture, record = self.fixture(), {}
        fixture.command.side_effect = ValueError("request EOF")
        with ExitStack() as stack:
            observer, close = self.mocks(stack)
            with self.assertRaisesRegex(ValueError, "acknowledged"):
                diagnostic.observe_guest_poweroff(fixture, "MARKER", record)
        observer.assert_called_once()
        fixture.capture_logs.assert_called_once()
        self.assertIn("request EOF", record["request_error"])
        close.assert_called_once()

    def test_request_interruption_immediately_returns_to_owned_cleanup(self):
        fixture = self.fixture()
        fixture.command.side_effect = InterruptedError("cancelled")
        with ExitStack() as stack:
            observer, close = self.mocks(stack)
            with self.assertRaises(InterruptedError):
                diagnostic.observe_guest_poweroff(fixture, "MARKER", {})
        observer.assert_not_called()
        close.assert_called_once_with(5)

    def test_observation_failure_keeps_logs_and_remains_primary(self):
        fixture, record = self.fixture(), {}
        fixture.capture_logs.side_effect = ValueError("logs unavailable")
        with ExitStack() as stack:
            observer, _ = self.mocks(stack)
            observer.side_effect = ValueError("observation timeout")
            with self.assertRaisesRegex(ValueError, "observation timeout"):
                diagnostic.observe_guest_poweroff(fixture, "MARKER", record)
        self.assertIn("logs unavailable", record["log_error"])
        self.assertIsNone(record["runtime_exit"])

    def test_replacement_vmm_is_rejected(self):
        fixture = self.fixture()
        fixture.runtime_pids.return_value = [456]
        with ExitStack() as stack:
            self.mocks(stack)
            with self.assertRaisesRegex(ValueError, "replacement"):
                diagnostic.observe_guest_poweroff(fixture, "MARKER", {})

    def test_observation_failure_remains_primary_with_replacement_evidence(self):
        fixture, record = self.fixture(), {}
        fixture.runtime_pids.return_value = [456]
        with ExitStack() as stack:
            observer, _ = self.mocks(stack)
            observer.side_effect = ValueError("observation timeout")
            with self.assertRaisesRegex(ValueError, "observation timeout"):
                diagnostic.observe_guest_poweroff(fixture, "MARKER", record)
        self.assertEqual(record["remaining_runtime_pids"], [456])
        self.assertIn("replacement", record["identity_error"])

    def test_logs_failure_cannot_certify_poweroff(self):
        fixture = self.fixture()
        fixture.capture_logs.side_effect = ValueError("logs unavailable")
        with ExitStack() as stack:
            self.mocks(stack)
            with self.assertRaisesRegex(ValueError, "logs unavailable"):
                diagnostic.observe_guest_poweroff(fixture, "MARKER", {})


if __name__ == "__main__":
    unittest.main()
