"""Portable negative controls; no runtime, store operation or VM is launched."""

import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import smoke_contract as contract
from smoke_full import build_account, expression, log_text, observe_vmm_exit, stop
import msb_smoke


def inspected():
    # Actual pinned-runtime output, with only names/reference normalized and
    # incidental top-level timestamps omitted. Not constructed by the oracle.
    return json.loads((Path(__file__).parent / "fixtures/msb-inspect.json").read_text())


def valid_stop():
    return {"cli_status": 0, "elapsed": 0.5, "runtime_exit": {"code": os.CLD_EXITED, "status": 0},
            "flush_marker": True, "logs": "", "signals": []}


DENIAL = (1, '{"url":"daemon"}\n',
          "error: access denied by the Nix daemon (check the `allowed-users` setting in the daemon)\n")
GREETING_PIPE = (1, '{"url":"daemon"}\n',
                 "error: cannot open connection to remote store 'daemon': write of 16 bytes: Broken pipe\n")


class ContractTests(unittest.TestCase):
    def cache_settings(self):
        return {
            "substituters": {"value": ["https://cache.nixos.org/", "https://install.determinate.systems/", contract.DEVENV_CACHE]},
            "trusted-substituters": {"value": ["https://cache.flakehub.com/", "https://install.determinate.systems/"]},
            "trusted-public-keys": {"value": ["supplier-default-key", contract.DEVENV_KEY]},
        }

    def test_shared_cache_retains_managed_defaults(self):
        settings = self.cache_settings()
        self.assertEqual(contract.cache_configuration(settings), {key: item["value"] for key, item in settings.items()})

    def test_shared_cache_rejects_absent_duplicate_wrong_key_or_extra_authority(self):
        changes = [
            ("substituters", []),
            ("substituters", [contract.DEVENV_CACHE] * 2),
            ("trusted-public-keys", []),
            ("trusted-public-keys", [contract.DEVENV_KEY] * 2),
            ("trusted-public-keys", [contract.DEVENV_KEY, "devenv.cachix.org-1:wrong"]),
            ("trusted-substituters", [contract.DEVENV_CACHE]),
            ("trusted-substituters", [contract.DEVENV_CACHE + "/"]),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                settings = self.cache_settings()
                settings[key]["value"] = value
                with self.assertRaises(ValueError):
                    contract.cache_configuration(settings)

    def test_shared_cache_rejects_missing_or_malformed_lists(self):
        for value in (None, "cache", [False]):
            settings = self.cache_settings()
            settings["substituters"]["value"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "type"):
                contract.cache_configuration(settings)
        with self.assertRaises(ValueError):
            contract.cache_configuration({})

    def test_exit_observer_waits_for_late_original_child_event_without_signals(self):
        fixture = Mock()
        fixture.supervisor.exit_events = []
        def refresh():
            if fixture.supervisor.refresh.call_count == 2:
                fixture.supervisor.exit_events.append({"pid": 123, "code": os.CLD_EXITED,
                                                       "status": 0, "observed": 11})
        fixture.supervisor.refresh.side_effect = refresh
        with patch("smoke_full.os.waitid", return_value=None), patch("smoke_full.time.sleep") as sleep:
            self.assertEqual(observe_vmm_exit(fixture, 123, 5, 10, 20), {"code": os.CLD_EXITED, "status": 0})
        fixture.limits.assert_called_once_with(20)
        sleep.assert_called_once_with(0.02)

    def test_exit_observer_accepts_only_the_pinned_pid_and_preserves_nonzero_status(self):
        fixture = Mock()
        fixture.supervisor.exit_events = []
        for pid in (123, 456):
            with self.subTest(pid=pid), patch("smoke_full.os.waitid", return_value=Mock(
                    si_pid=pid, si_code=os.CLD_KILLED, si_status=9)):
                if pid == 123:
                    self.assertEqual(observe_vmm_exit(fixture, 123, 5, 10, 20),
                                     {"code": os.CLD_KILLED, "status": 9})
                else:
                    with self.assertRaisesRegex(ValueError, "identity"):
                        observe_vmm_exit(fixture, 123, 5, 10, 20)

    def test_exit_observer_rejects_ambiguous_or_unobserved_exit(self):
        fixture = Mock()
        event = {"pid": 123, "code": os.CLD_EXITED, "status": 0, "observed": 11}
        fixture.supervisor.exit_events = [event, event]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            observe_vmm_exit(fixture, 123, 5, 10, 20)
        fixture.supervisor.exit_events = [dict(event, observed=9), dict(event, pid=456)]
        fixture.limits.side_effect = ValueError("fixture deadline")
        with patch("smoke_full.os.waitid", side_effect=ChildProcessError), \
             self.assertRaisesRegex(ValueError, "deadline"):
            observe_vmm_exit(fixture, 123, 5, 10, 20)

    def test_stop_retains_complete_fallback_logs_when_exit_observation_fails(self):
        fixture = Mock()
        fixture.name, fixture.deadline = "owned", 100
        fixture.report = {"stops": []}
        fixture.runtime_pids.return_value = [123]
        fixture.supervisor.pidfds = {123: 4}
        fixture.supervisor.signal_events = []
        fixture.command.return_value = (0, "", "")
        fixture.capture_logs.return_value = json.dumps({"s": "system", "d": "flush window elapsed, triggering host exit\n", "e": None})
        with patch("smoke_full.os.dup", return_value=5), patch("smoke_full.os.close") as close, \
             patch("shutdown_diagnostic.snapshot"), \
             patch("smoke_full.os.waitid", return_value=None), patch("smoke_full.time.monotonic", side_effect=[10, 11, 12]), \
             patch("smoke_full.observe_vmm_exit", side_effect=ValueError("fixture deadline")), \
             self.assertRaisesRegex(ValueError, "deadline"):
            stop(fixture, "marker")
        close.assert_called_once_with(5)
        record = fixture.report["stops"][0]
        self.assertEqual(record["runtime_pid"], 123)
        self.assertIsNone(record["runtime_exit"])
        self.assertIn("triggering host exit", record["logs"])
        self.assertIn("deadline", record["observation_error"])
        self.assertEqual(record["elapsed"], 2)

    def test_each_normal_stop_takes_same_snapshot_before_host_request(self):
        fixture = Mock()
        fixture.name, fixture.deadline = "owned", 100
        fixture.report = {"stops": []}
        fixture.runtime_pids.side_effect = [[123], [], [456], []]
        fixture.supervisor.pidfds = {123: 4, 456: 7}
        fixture.supervisor.signal_events = []
        events = []
        fixture.command.side_effect = lambda *args, **kwargs: (events.append("host-stop") or (0, "", ""))
        fixture.capture_logs.return_value = json.dumps({"s": "system", "d": "marker\n"})
        def snapshot(_fixture, record):
            events.append("snapshot")
            record["pre_stop"] = {"recorded": True}
        with patch("shutdown_diagnostic.snapshot", side_effect=snapshot) as snap, \
             patch("smoke_full.os.dup", side_effect=[5, 8]), patch("smoke_full.os.close"), \
             patch("smoke_full.os.waitid", return_value=None), \
             patch("smoke_full.time.monotonic", side_effect=[10, 11, 12, 20, 21, 22]), \
             patch("smoke_full.observe_vmm_exit", return_value={"code": os.CLD_EXITED, "status": 0}):
            stop(fixture, "marker")
            stop(fixture, "marker")
        self.assertEqual(snap.call_count, 2)
        self.assertEqual(events, ["snapshot", "host-stop", "snapshot", "host-stop"])
        self.assertTrue(all(record["pre_stop"] == {"recorded": True} for record in fixture.report["stops"]))

    def test_normal_stop_preserves_observation_failure_before_log_failure(self):
        for observation_error in (ValueError("observation timeout"), None):
            fixture = Mock()
            fixture.name, fixture.deadline = "owned", 100
            fixture.report = {"stops": []}
            fixture.runtime_pids.return_value = [123]
            fixture.supervisor.pidfds = {123: 4}
            fixture.supervisor.signal_events = []
            fixture.command.return_value = (0, "", "")
            fixture.capture_logs.side_effect = ValueError("logs unavailable")
            expected = "observation timeout" if observation_error else "logs unavailable"
            with self.subTest(expected=expected), patch("shutdown_diagnostic.snapshot"), \
                 patch("smoke_full.os.dup", return_value=5), patch("smoke_full.os.close") as close, \
                 patch("smoke_full.os.waitid", return_value=None), \
                 patch("smoke_full.time.monotonic", side_effect=[10, 11, 12]), \
                 patch("smoke_full.observe_vmm_exit", side_effect=observation_error,
                       return_value={"code": os.CLD_EXITED, "status": 0}), \
                 self.assertRaisesRegex(ValueError, expected):
                stop(fixture, "marker")
            record = fixture.report["stops"][0]
            self.assertIn("logs unavailable", record["log_error"])
            if observation_error:
                self.assertIn("observation timeout", record["observation_error"])
            self.assertEqual(record["signals"], [])
            close.assert_called_once_with(5)

    def test_normal_stop_snapshot_failure_does_not_issue_shutdown(self):
        fixture = Mock()
        fixture.report = {"stops": []}
        with patch("shutdown_diagnostic.snapshot", side_effect=ValueError("snapshot failure")), \
             self.assertRaisesRegex(ValueError, "snapshot failure"):
            stop(fixture, "marker")
        fixture.command.assert_not_called()

    def denial_fixture(self, results):
        fixture = object.__new__(msb_smoke.Fixture)
        fixture.spec = {"engine": "/pinned/engine", "systemd": "/pinned/systemd"}
        fixture.report = {"root_trusted": True, "qa_trusted": False}
        fixture.execute = Mock(side_effect=results)
        fixture.daemon_failure_diagnostics = Mock()
        return fixture

    def test_retryable_denial_transport_requires_exact_observed_response(self):
        self.assertTrue(contract.retryable_denial_transport(*GREETING_PIPE))
        cases = [(0, *GREETING_PIPE[1:]), (True, *GREETING_PIPE[1:]),
                 (2, *GREETING_PIPE[1:]), (1, '{"url":"daemon","trusted":false}\n', GREETING_PIPE[2]),
                 (1, '{"url":"daemon"}', GREETING_PIPE[2]),
                 (1, GREETING_PIPE[1], GREETING_PIPE[2].replace("16", "8")),
                 (1, GREETING_PIPE[1], GREETING_PIPE[2] + "another error\n"),
                 (1, GREETING_PIPE[1], "connection reset by peer\n"), DENIAL]
        for result in cases:
            with self.subTest(result=result):
                self.assertFalse(contract.retryable_denial_transport(*result))

    def test_nobody_retry_still_requires_explicit_denial_and_preserves_each_attempt(self):
        for failures in range(3):
            fixture = self.denial_fixture([GREETING_PIPE] * failures + [DENIAL])
            fixture.check_nobody_denial()
            self.assertTrue(fixture.report["nobody_denied"])
            attempts = fixture.report["nobody_attempts"][0]
            self.assertEqual(len(attempts), failures + 1)
            self.assertEqual([item["classification"] for item in attempts],
                             ["retryable_transport_not_denial"] * failures + ["explicit_authorization_denial"])
            for call in fixture.execute.call_args_list:
                self.assertEqual(call.args, ("/pinned/engine/bin/nix store info --store daemon --json\n",))
                self.assertEqual(call.kwargs, {"user": "65534:65534", "timeout": 5, "check": False})
            fixture.daemon_failure_diagnostics.assert_not_called()

    def test_three_transport_failures_are_not_denial(self):
        fixture = self.denial_fixture([GREETING_PIPE] * 3 + [DENIAL])
        fixture.report["nobody_denied"] = True
        fixture.report["nobody_attempts"] = [[{"classification": "earlier_boot_denial"}]]
        with self.assertRaisesRegex(ValueError, "three transport failures"):
            fixture.check_nobody_denial()
        self.assertFalse(fixture.report["nobody_denied"])
        self.assertEqual(fixture.execute.call_count, 3)
        self.assertEqual(len(fixture.report["nobody_attempts"][1]), 3)
        self.assertEqual(fixture.report["nobody_attempts"][0], [{"classification": "earlier_boot_denial"}])
        fixture.daemon_failure_diagnostics.assert_called_once_with()

    def test_nobody_success_and_other_errors_never_retry(self):
        for result in [(0, '{"url":"daemon","trusted":false}', ""),
                       (1, '{"url":"daemon"}', "connection refused"),
                       (1, '{"url":"daemon","trusted":false}', DENIAL[2]),
                       (True, DENIAL[1], DENIAL[2]), (2, *GREETING_PIPE[1:])]:
            fixture = self.denial_fixture([result, DENIAL])
            with self.subTest(result=result), self.assertRaises(ValueError):
                fixture.check_nobody_denial()
            self.assertEqual(fixture.execute.call_count, 1)
            self.assertFalse(fixture.report["nobody_denied"])
            self.assertEqual(fixture.report["nobody_attempts"][0][0]["stderr"], result[2])
            fixture.daemon_failure_diagnostics.assert_called_once_with()

    def test_nobody_execution_and_diagnostic_errors_preserve_primary_failure(self):
        fixture = self.denial_fixture([TimeoutError("primary timeout")])
        fixture.daemon_failure_diagnostics.side_effect = RuntimeError("secondary journal failure")
        with self.assertRaisesRegex(TimeoutError, "primary timeout"):
            fixture.check_nobody_denial()
        self.assertFalse(fixture.report["nobody_denied"])
        self.assertEqual(fixture.execute.call_count, 1)
        self.assertIn("primary timeout", fixture.report["nobody_attempts"][0][0]["execution_error"])
        self.assertIn("secondary journal failure", fixture.report["daemon_failure_diagnostics"][0]["diagnostic_error"])

    def test_daemon_failure_diagnostics_are_bounded_read_only_and_keep_nonzero_results(self):
        fixture = object.__new__(msb_smoke.Fixture)
        fixture.spec = {"systemd": "/pinned/systemd", "engine": "/pinned/engine"}
        fixture.report = {"error": "primary"}
        fixture.execute = Mock(side_effect=[(0, "ActiveState=active\n", ""),
                                            (1, "", "journal unavailable"), (0, '{"allowed-users":{}}', "")])
        fixture.daemon_failure_diagnostics()
        snapshot = fixture.report["daemon_failure_diagnostics"][0]
        self.assertEqual(snapshot["journal"]["exit_status"], 1)
        self.assertEqual(fixture.report["error"], "primary")
        for call in fixture.execute.call_args_list:
            self.assertEqual(call.kwargs, {"timeout": 5, "check": False})
        commands = [call.args[0] for call in fixture.execute.call_args_list]
        self.assertIn("systemctl show nix-daemon.service", commands[0])
        self.assertIn("journalctl --unit=nix-daemon.service --lines=100", commands[1])
        self.assertNotIn("--boot", commands[1])
        self.assertEqual(commands[2], "/pinned/engine/bin/nix config show --json\n")

    def test_daemon_diagnostic_exception_and_oversize_are_retained_without_success(self):
        fixture = object.__new__(msb_smoke.Fixture)
        fixture.spec = {"systemd": "/pinned/systemd", "engine": "/pinned/engine"}
        fixture.report = {"error": "primary"}
        fixture.execute = Mock(side_effect=[TimeoutError("unit timeout"),
                                            (0, "x" * (128 * 1024 + 1), ""), (0, "{}", "")])
        fixture.daemon_failure_diagnostics()
        snapshot = fixture.report["daemon_failure_diagnostics"][0]
        self.assertIn("unit timeout", snapshot["unit"]["diagnostic_error"])
        self.assertIn("budget", snapshot["journal"]["diagnostic_error"])
        self.assertEqual(snapshot["config"]["stdout"], "{}")
        self.assertEqual(fixture.report["error"], "primary")
        self.assertNotIn("nobody_denied", fixture.report)

    def test_activation_records_positive_trust_before_failed_negative(self):
        fixture = object.__new__(msb_smoke.Fixture)
        fixture.spec = {"systemd": "/pinned/systemd", "engine": "/pinned/engine", "nixd": "/pinned/nixd",
                        "coreutils": "/pinned/coreutils", "utilLinux": "/pinned/util", "toplevel": "/pinned/system",
                        "registration": "/pinned/registration", "roots": ["/pinned/payload"]}
        fixture.nonce, fixture.name, fixture.image = "fixture", "owned", "image"
        fixture.deadline = msb_smoke.time.monotonic() + 30
        fixture.report = {}
        fixture.boot_diagnostics = Mock()
        fixture.command = Mock(return_value=(0, "{}", ""))
        fixture.runtime_pids = Mock(return_value=[123])
        fixture.setup_qa_user = Mock()
        fixture.guest_json = Mock(side_effect=[{"trusted": True}, {"trusted": False}])
        fixture.check_nobody_denial = Mock(side_effect=ValueError("negative failed"))

        def execute(body, **_kwargs):
            if "readlink -f /proc/1/exe" in body:
                return 0, "ACTIVATED:fixture\n", ""
            if "query --requisites" in body:
                return 0, "/pinned/payload\n", ""
            if "sha256sum" in body:
                return 0, "expected-ca\n", ""
            if "--property ExecStart" in body:
                return 0, "/pinned/nixd/bin/determinate-nixd /pinned/engine/bin", ""
            if "id -u" in body:
                return 0, "65534\n65534\n65534\n", ""
            return 0, "", ""

        fixture.execute = Mock(side_effect=execute)
        with patch("msb_smoke.validate_inspect"), patch("msb_smoke.digest", return_value="expected-ca"), \
             patch("msb_smoke.Path.read_text", return_value="/pinned/payload\n"):
            with self.assertRaisesRegex(ValueError, "negative failed"):
                fixture.activation()
        self.assertTrue(fixture.report["root_trusted"])
        self.assertFalse(fixture.report["qa_trusted"])
        self.assertEqual([call.kwargs["user"] for call in fixture.guest_json.call_args_list], [None, "1000:100"])
        fixture.check_nobody_denial.assert_called_once_with()

    def test_build_account_uses_guest_system_getent_not_glibc_bin_output(self):
        fixture = Mock(spec=["execute", "spec"])
        fixture.spec = {"glibc": "/absent/glibc-bin"}
        fixture.execute.return_value = (0, "nixbld1:x:30001:30000::/var/empty:/bin/nologin\n", "")
        self.assertEqual(build_account(fixture, 30001), fixture.execute.return_value[1])
        fixture.execute.assert_called_once_with("/run/current-system/sw/bin/getent passwd 30001\n")
        for uid in (0, -1, 2**32, True, "30001"):
            fixture.execute.reset_mock()
            with self.assertRaisesRegex(ValueError, "account UID"):
                build_account(fixture, uid)
            fixture.execute.assert_not_called()

    def test_failed_full_sysctl_replay_is_bounded_and_retains_nonzero_result(self):
        fixture = object.__new__(msb_smoke.Fixture)
        fixture.report = {"error": "primary", "boot_diagnostics": [{"unit_health": "failed_units_observed"}]}
        fixture.spec = {"systemd": "/nix/store/systemd"}
        fixture.inspect_running_vmm = Mock(return_value=101)
        fixture.execute = Mock(return_value=(1, "", "denied key"))
        fixture.capture_logs = Mock()
        fixture.failed_full_sysctl_replay(101)
        fixture.execute.assert_called_once_with(
            "SYSTEMD_LOG_LEVEL=debug SYSTEMD_LOG_TARGET=console /nix/store/systemd/lib/systemd/systemd-sysctl\n",
            timeout=5, check=False)
        self.assertEqual(fixture.report["sysctl_replay"]["exit_status"], 1)
        self.assertEqual(fixture.report["error"], "primary")
        self.assertEqual(fixture.report["boot_diagnostics"][0]["unit_health"], "failed_units_observed")
        fixture.capture_logs.assert_called_once_with()

    def test_failed_full_sysctl_replay_refuses_stopped_or_replaced_vmm(self):
        for observed in (ValueError("not Running"), 102):
            fixture = object.__new__(msb_smoke.Fixture)
            fixture.report = {"error": "primary"}
            fixture.spec = {"systemd": "/nix/store/systemd"}
            fixture.inspect_running_vmm = Mock(side_effect=observed) if isinstance(observed, Exception) else Mock(return_value=observed)
            fixture.execute = Mock()
            fixture.failed_full_sysctl_replay(101)
            fixture.execute.assert_not_called()
            self.assertIn("diagnostic_error", fixture.report["sysctl_replay"])
            self.assertEqual(fixture.report["error"], "primary")

    def test_failed_full_sysctl_replay_preserves_failure_on_oversized_diagnostics(self):
        fixture = object.__new__(msb_smoke.Fixture)
        fixture.report = {"error": "primary"}
        fixture.spec = {"systemd": "/nix/store/systemd"}
        fixture.inspect_running_vmm = Mock(return_value=101)
        fixture.execute = Mock(return_value=(1, "x" * (128 * 1024 + 1), ""))
        fixture.capture_logs = Mock()
        fixture.failed_full_sysctl_replay(101)
        self.assertIn("output budget", fixture.report["sysctl_replay"]["diagnostic_error"])
        self.assertNotIn("stdout", fixture.report["sysctl_replay"])
        fixture.capture_logs.assert_not_called()
        self.assertEqual(fixture.report["error"], "primary")

    def test_environment_has_no_ambient_state_or_credentials(self):
        with patch.dict(os.environ, {"SSH_AUTH_SOCK": "secret", "MSB_CONTEXT": "remote", "MSB_HOME": "live", "OPENAI_API_KEY": "secret"}):
            env = contract.isolated_environment(Path("/owned"), Path("/nix/store/runtime/bin/msb"))
        self.assertEqual(env["MSB_HOME"], "/owned/msb")
        self.assertEqual(env["MSB_CONFIG_PATH"], "/owned/msb.json")
        self.assertEqual(env["MSB_BACKEND"], "local")
        self.assertNotIn("MSB_CONTEXT", env)
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_exact_active_profile(self):
        contract.validate_inspect(inspected(), "fixture", "localhost:9/base:fixture")

    def test_only_exact_source_default_guest_tmpfs_is_allowed(self):
        original = inspected()["active_config"]["mounts"][0]
        for field, value in [("host", "/private"), ("type", "Bind"), ("guest", "/elsewhere"),
                             ("size_mib", 1024), ("extra", None)]:
            changed = copy.deepcopy(original)
            changed[field] = value
            with self.subTest(field=field):
                record = inspected()
                record["active_config"]["mounts"] = [changed]
                with self.assertRaisesRegex(ValueError, "mount"):
                    contract.validate_inspect(record, "fixture", "localhost:9/base:fixture")
        for mounts in ([], [original, original]):
            record = inspected()
            record["config"]["mounts"] = mounts
            with self.assertRaisesRegex(ValueError, "mount"):
                contract.validate_inspect(record, "fixture", "localhost:9/base:fixture")
        for key, value in [("readonly", True), ("readonly", 0), ("unknown", False)]:
            record = inspected()
            record["config"]["mounts"][0]["options"][key] = value
            with self.assertRaisesRegex(ValueError, "mount"):
                contract.validate_inspect(record, "fixture", "localhost:9/base:fixture")

    def test_no_nondefault_or_unknown_network_capabilities(self):
        changes = [
            (("tls", "enabled"), True), (("tls", "enabled"), 0),
            (("tls", "verify_upstream"), False), (("tls", "bypass"), ["*"]),
            (("tls", "intercept_ca", "cert_path"), "/private/ca.pem"),
            (("tls", "intercept_ca", "key_path"), "/private/key.pem"),
            (("tls", "upstream_ca_cert"), ["/private/ca.pem"]),
            (("tls", "scoped_upstream_ca_cert"), [{"path": "/private/ca.pem"}]),
            (("tls", "scoped_verify_upstream"), [{"verify": False}]),
            (("tls", "unknown"), None), (("secrets", "secrets"), [{"source": "private"}]),
            (("secrets", "on_violation"), "passthrough"), (("secrets", "unknown"), None),
            (("dns", "nameservers"), ["192.0.2.1"]), (("dns", "rebind_protection"), False),
            (("interface", "ipv4_pool"), "192.0.2.0/24"), (("ports",), [8080]),
            (("policy", "rules"), [{"action": "allow"}]), (("policy", "default_ingress"), "allow"),
            (("trust_host_cas",), True), (("enabled",), False), (("ssh",), {}),
            (("outbound_proxy",), {"address": "192.0.2.1:1080"}), (("unknown",), None),
        ]
        for path, value in changes:
            with self.subTest(path=path, value=value):
                record = inspected()
                current = record["active_config"]["network"]
                for key in path[:-1]:
                    current = current[key]
                current[path[-1]] = value
                with self.assertRaisesRegex(ValueError, "network"):
                    contract.validate_inspect(record, "fixture", "localhost:9/base:fixture")

    def test_mount_port_secret_init_and_resource_negatives(self):
        changes = [
            ("mounts", [{"Bind": {"host": "/private"}}]), ("patches", ["copy"]),
            ("vsock", {"host": "socket"}), ("init", {"cmd": "/bin/sh"}),
            ("pull_policy", "Always"), ("security_profile", "Restricted"),
            ("resources", {"cpus": 4}), ("runtime", {"cmd": ["surprise"]}),
            ("image", {"Bind": {"path": "/"}}), ("lifecycle", {"max_duration_secs": None}),
            ("env", [{"key": "API_KEY", "value": "not-a-real-key"}]),
        ]
        for key, value in changes:
            with self.subTest(key=key):
                record = inspected()
                record["active_config"][key] = value
                with self.assertRaises(ValueError):
                    contract.validate_inspect(record, "fixture", "localhost:9/base:fixture")
        for key, value in [("ports", [8080]), ("policy", {"default_egress": "allow"}),
                           ("secrets", {"key": "private"}), ("outbound_proxy", {"address": "remote"}),
                           ("trust_host_cas", True)]:
            with self.subTest(network=key):
                record = inspected()
                record["config"]["network"][key] = value
                with self.assertRaises(ValueError):
                    contract.validate_inspect(record, "fixture", "localhost:9/base:fixture")

    def test_stopped_or_pending_cannot_be_ready(self):
        for key, value in [("status", "Stopped"), ("pending_changes", ["init"]), ("active_config", None)]:
            record = inspected()
            record[key] = value
            with self.assertRaises(ValueError):
                contract.validate_inspect(record, "fixture", "localhost:9/base:fixture")

    def test_namespace_uid_is_mapped_before_comparing_user(self):
        self.assertEqual(contract.mapped_uid(1000, "1000 30001 1\n", "nixbld1:x:30001:30000::/empty:/nologin"), 30001)
        for mapping, uid, account in [("1000 65534 1", 1000, "nobody:x:65534:65534::/:/nologin"),
                                      ("1000 1000 1", 1000, "nixbld-test:x:1000:100::/:/nologin"),
                                      ("1000 0 1", 1000, "root:x:0:0::/:/bin/sh"),
                                      ("1000 30001 1\n1000 30002 1", 1000, ""),
                                      ("0 30001 1", 0, ""), ("1 -1 0", 1, "")]:
            with self.subTest(mapping=mapping):
                with self.assertRaises(ValueError):
                    contract.mapped_uid(uid, mapping, account)

    def test_declared_inputs_modern_and_legacy(self):
        paths = ["/nix/store/" + "0" * 32 + "-" + name for name in ("bash", "script", "declared")]
        contract.derivation_inputs({"drv": {"inputSrcs": paths}}, paths)
        contract.derivation_inputs({"derivations": {"drv": {"inputs": {"srcs": [path[11:] for path in paths]}}}}, paths)
        with self.assertRaises(ValueError):
            contract.derivation_inputs({"drv": {"inputSrcs": paths[:2]}}, paths)

    def test_stop_requires_flush_exit_deadline_and_no_fallback(self):
        contract.validate_stop(valid_stop())
        for key, value in [("cli_status", 1), ("elapsed", 8), ("runtime_exit", None),
                           ("runtime_exit", {"code": os.CLD_KILLED, "status": 9}),
                           ("flush_marker", False), ("signals", [9]),
                           ("logs", "reboot: Power off not available: System halted instead"),
                           ("logs", "flush window elapsed, triggering host exit")]:
            with self.subTest(key=key):
                stop = valid_stop()
                stop[key] = value
                with self.assertRaises(ValueError):
                    contract.validate_stop(stop)

    def test_activation_is_explicitly_not_full_acceptance(self):
        report = {"mode": "activation", "activation": True, "registration": True,
                  "root_trusted": True, "qa_trusted": False, "nobody_denied": True,
                  "cleanup": {"empty_store": True, "children_empty": True, "forced": False, "errors": []}}
        self.assertEqual(contract.verdict(report), "activation_only_passed")
        report["mode"] = "full"
        with self.assertRaises(ValueError):
            contract.verdict(report)
        report.update(builds=["ordinary", "untrusted-overrides"], daemon_restart=True,
                      persistent_store=True, stops=[valid_stop(), valid_stop()],
                      boot_diagnostics=[{"unit_health": "no_failed_units_reported",
                                         "kernel_pid_max": {"exit_status": 0, "stdout": "32768\n", "stderr": ""}}] * 2)
        self.assertEqual(contract.verdict(report), "full_acceptance_passed")
        for key in ("activation", "registration", "persistent_store", "daemon_restart"):
            bad = copy.deepcopy(report)
            bad[key] = False
            with self.assertRaises(ValueError):
                contract.verdict(bad)
        bad = copy.deepcopy(report)
        bad["cleanup"]["forced"] = True
        with self.assertRaises(ValueError):
            contract.verdict(bad)
        for key, value in (("qa_trusted", True), ("nobody_denied", False), ("qa_trusted", 0)):
            bad = copy.deepcopy(report)
            bad[key] = value
            with self.assertRaisesRegex(ValueError, "trust mismatch"):
                contract.verdict(bad)
        for health in (None, [], [{}], [{"unit_health": "no_failed_units_reported"}],
                       [{"unit_health": "no_failed_units_reported"}, {"unit_health": "failed_units_observed"}],
                       [{"unit_health": "unavailable"}, {"unit_health": "no_failed_units_reported"}]):
            with self.subTest(health=health):
                bad = copy.deepcopy(report)
                bad["boot_diagnostics"] = health
                with self.assertRaisesRegex(ValueError, "boot-unit health"):
                    contract.verdict(bad)
                self.assertEqual(bad["builds"], ["ordinary", "untrusted-overrides"])
                self.assertTrue(bad["persistent_store"])
                self.assertEqual(len(bad["stops"]), 2)
        for observed in (None, {}, {"exit_status": 1, "stdout": "32768\n", "stderr": ""},
                         {"exit_status": False, "stdout": "32768\n", "stderr": ""},
                         {"exit_status": 0, "stdout": "0\n", "stderr": ""},
                         {"exit_status": 0, "stdout": "unknown\n", "stderr": ""}):
            bad = copy.deepcopy(report)
            bad["boot_diagnostics"][0]["kernel_pid_max"] = observed
            with self.subTest(observed=observed), self.assertRaisesRegex(ValueError, "PID range"):
                contract.verdict(bad)
        report["error"] = "primary build failure"
        with self.assertRaisesRegex(ValueError, "primary failure"):
            contract.verdict(report)

    def test_log_framing_does_not_treat_unknown_encoding_as_text(self):
        self.assertEqual(log_text('{"s":"system","d":"RkxVU0g=","e":"b64"}'), "FLUSH")
        with self.assertRaises(ValueError):
            log_text('{"s":"system","d":"FLUSH","e":"arbitrary"}')
        with self.assertRaises(ValueError):
            log_text("not JSON")
        self.assertEqual(log_text('{"s":"stdout","d":"FLUSH"}', source="system"), "")
        self.assertEqual(log_text('{"s":"system","d":"FLUSH"}', source="system"), "FLUSH")

    def test_builder_keeps_context_and_positive_control_target(self):
        spec = {"bash": "/nix/store/" + "0" * 32 + "-bash", "coreutils": "/nix/store/" + "1" * 32 + "-coreutils"}
        text = expression(spec, "fixture")
        self.assertIn("builtins.storePath", text)
        self.assertIn("${utils}/bin/id", text)
        self.assertIn("/dev/tcp/127.0.0.1/19877", text)
        self.assertIn("inputs = map toString [ bash script declared ]", text)

    def test_cleanup_snapshots_then_quiesces_before_removal(self):
        fixture = msb_smoke.Fixture.__new__(msb_smoke.Fixture)
        fixture.report = {"error": "original failure"}
        fixture.attempted = True
        fixture.name = "owned"
        events = []
        fixture.supervisor = Mock(signal_events=[], exit_events=[])
        fixture.supervisor.snapshot.return_value = []
        fixture.supervisor.quiesce.side_effect = lambda **_: events.append("quiesce")
        fixture.supervisor.children.return_value = set()
        fixture.capture_logs = Mock(side_effect=lambda **_: events.append("logs"))
        fixture.command = Mock(side_effect=lambda argv, **_: (events.append(argv[0]) or 0, "[]", ""))
        fixture.cleanup()
        self.assertEqual(events[:2], ["logs", "quiesce"])
        self.assertLess(events.index("quiesce"), events.index("remove"))
        self.assertEqual(fixture.report["error"], "original failure")
        self.assertEqual(fixture.report["cleanup"]["errors"], [])

    def test_log_failure_does_not_skip_stop_or_owned_cleanup(self):
        fixture = msb_smoke.Fixture.__new__(msb_smoke.Fixture)
        fixture.report, fixture.attempted, fixture.name = {}, True, "owned"
        fixture.supervisor = Mock(signal_events=[], exit_events=[])
        fixture.supervisor.snapshot.return_value = []
        fixture.supervisor.children.return_value = set()
        fixture.capture_logs = Mock(side_effect=ValueError("log failure"))
        fixture.command = Mock(return_value=(0, "[]", ""))
        fixture.cleanup()
        self.assertEqual([call.args[0][0] for call in fixture.command.call_args_list], ["stop", "remove", "list"])
        self.assertEqual(fixture.report["cleanup"]["errors"], ["log failure", "post-stop logs: log failure"])
        fixture.supervisor.quiesce.assert_called_once()
        fixture.supervisor.close.assert_called_once()

    def test_close_escalation_is_a_failed_cleanup(self):
        fixture = msb_smoke.Fixture.__new__(msb_smoke.Fixture)
        fixture.report, fixture.attempted = {}, False
        fixture.supervisor = Mock(signal_events=[], exit_events=[])
        fixture.supervisor.snapshot.return_value = []
        fixture.supervisor.children.return_value = set()
        fixture.supervisor.close.side_effect = lambda: fixture.supervisor.signal_events.append({"signal": 15})
        fixture.command = Mock(return_value=(0, "[]", ""))
        fixture.cleanup()
        self.assertTrue(fixture.report["cleanup"]["forced"])
        self.assertEqual(fixture.report["cleanup_diagnostics"]["signal_events"], [{"signal": 15}])

    def test_cleanup_retains_post_stop_owned_identity_and_exit_diagnostics(self):
        fixture = msb_smoke.Fixture.__new__(msb_smoke.Fixture)
        fixture.report, fixture.attempted, fixture.name = {}, True, "owned"
        fixture.supervisor = Mock(signal_events=[], exit_events=[{"pid": 123, "code": os.CLD_EXITED, "status": 0}])
        fixture.supervisor.snapshot.side_effect = [[{"pid": 123, "executable": "/runtime"}],
                                                   [{"pid": 456, "executable": "/helper"}]]
        fixture.supervisor.children.return_value = set()
        fixture.capture_logs = Mock()
        fixture.command = Mock(return_value=(0, "[]", ""))
        fixture.cleanup()
        evidence = fixture.report["cleanup_diagnostics"]
        self.assertEqual(evidence["snapshots"][1]["stage"], "after_stop_before_quiescence")
        self.assertEqual(evidence["snapshots"][1]["owned"][0]["pid"], 456)
        self.assertEqual(evidence["exit_events"][0]["pid"], 123)
        self.assertEqual(fixture.capture_logs.call_count, 2)
        self.assertFalse(fixture.report["cleanup"]["forced"])

    def test_host_memory_accounting_is_required(self):
        self.assertEqual(msb_smoke.available_memory("MemTotal: 999 kB\nMemAvailable: 123 kB\n"), 123 * 1024)
        for text in ("", "MemAvailable: 1 B", "MemAvailable: -1 kB", "MemAvailable: 1 kB\nMemAvailable: 2 kB"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                msb_smoke.available_memory(text)

    def test_resource_admission_retains_memory_and_disk_reserve(self):
        gib = msb_smoke.GIB
        msb_smoke.resource_admission(22 * gib, 8 * gib)
        for free, memory in ((22 * gib - 1, 8 * gib), (22 * gib, 8 * gib - 1)):
            with self.subTest(free=free, memory=memory), self.assertRaisesRegex(ValueError, "admission"):
                msb_smoke.resource_admission(free, memory)
        self.assertEqual(msb_smoke.DISK_ADMISSION - msb_smoke.GROWTH_LIMIT, 18 * gib)
        self.assertEqual(msb_smoke.DISK_FLOOR, 17 * gib)
        self.assertEqual(msb_smoke.SCRATCH_LIMIT, 4 * gib)

    def resource_fixture(self):
        fixture = object.__new__(msb_smoke.Fixture)
        fixture.root, fixture.report, fixture.logs = Path("/owned"), {}, []
        fixture.initial_free = msb_smoke.DISK_ADMISSION
        fixture.deadline, fixture.next_scan = float("inf"), float("inf")
        return fixture

    def test_global_growth_stop_is_independent_of_owned_scan_and_disk_floor(self):
        fixture = self.resource_fixture()
        free = fixture.initial_free - msb_smoke.GROWTH_LIMIT
        with patch.object(msb_smoke.shutil, "disk_usage", return_value=Mock(free=free)), \
             patch.object(msb_smoke, "available_memory", return_value=8 * msb_smoke.GIB):
            fixture.limits()
        self.assertEqual(fixture.report["maximum_sampled_global_growth_bytes"], msb_smoke.GROWTH_LIMIT)
        with patch.object(msb_smoke.shutil, "disk_usage", return_value=Mock(free=free - 1)), \
             self.assertRaisesRegex(ValueError, "global disk growth"):
            fixture.limits()
        self.assertEqual(fixture.report["maximum_sampled_global_growth_bytes"], msb_smoke.GROWTH_LIMIT + 1)
        fixture.initial_free = msb_smoke.DISK_FLOOR
        with patch.object(msb_smoke.shutil, "disk_usage", return_value=Mock(free=msb_smoke.DISK_FLOOR - 1)), \
             self.assertRaisesRegex(ValueError, "disk floor"):
            fixture.limits()

    def test_owned_scratch_stop_retains_observed_size_despite_global_free_space(self):
        for allocated in (msb_smoke.SCRATCH_LIMIT, msb_smoke.SCRATCH_LIMIT + 512):
            fixture = self.resource_fixture()
            fixture.next_scan = 0
            with self.subTest(allocated=allocated), \
                 patch.object(msb_smoke.shutil, "disk_usage", return_value=Mock(free=fixture.initial_free)), \
                 patch.object(msb_smoke, "available_memory", return_value=8 * msb_smoke.GIB), \
                 patch.object(msb_smoke.os, "walk", return_value=[("/owned", [], ["payload"])]), \
                 patch.object(Path, "lstat", return_value=Mock(st_blocks=allocated // 512)):
                if allocated == msb_smoke.SCRATCH_LIMIT:
                    fixture.limits()
                else:
                    with self.assertRaisesRegex(ValueError, "scratch quota"):
                        fixture.limits()
                self.assertEqual(fixture.report["peak_sampled_scratch_bytes"], allocated)

    def test_import_decompression_is_bounded_and_never_extracts_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, target = root / "image.gz", root / "image.tar"
            payload = b"synthetic raw tar bytes"
            archive.write_bytes(gzip.compress(payload))
            limits = Mock()
            result = msb_smoke.unpack_archive(archive, target, limits, maximum=len(payload))
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(result["bytes"], len(payload))
            self.assertGreater(limits.call_count, 1)
            with self.assertRaises(FileExistsError):
                msb_smoke.unpack_archive(archive, target, limits)
            with self.assertRaisesRegex(ValueError, "budget"):
                msb_smoke.unpack_archive(archive, root / "too-large.tar", limits, maximum=len(payload) - 1)
            damaged = bytearray(archive.read_bytes())
            damaged[-8] ^= 1
            archive.write_bytes(damaged)
            with self.assertRaises(gzip.BadGzipFile):
                msb_smoke.unpack_archive(archive, root / "damaged.tar", limits)

    def test_full_log_retrieval_requires_successful_command(self):
        fixture = msb_smoke.Fixture.__new__(msb_smoke.Fixture)
        fixture.name = "owned"
        fixture.command = Mock(return_value=(0, "logs", ""))
        self.assertEqual(fixture.capture_logs(), "logs")
        self.assertTrue(fixture.command.call_args.kwargs["check"])

    def test_boot_diagnostics_retain_failed_units_without_claiming_readiness(self):
        fixture = msb_smoke.Fixture.__new__(msb_smoke.Fixture)
        fixture.spec, fixture.report = {"systemd": "/pinned/systemd", "coreutils": "/pinned/coreutils"}, {}
        fixture.execute = Mock(side_effect=[(0, "systemd-sysctl.service loaded failed failed\n", ""),
                                            (0, "sysctl: exact diagnostic\n", ""),
                                            (0, "Result=exit-code\n", ""), (0, "kernel.pid_max=4194304\n", ""),
                                            (0, "earlier boot diagnostic\n", ""), (0, "boot metadata\n", ""),
                                            (0, "tagged sysctl diagnostic\n", ""), (0, "other warning\n", ""),
                                            (0, "32768\n", "")])
        fixture.boot_diagnostics()
        evidence = fixture.report["boot_diagnostics"][0]
        self.assertEqual(evidence["unit_health"], "failed_units_observed")
        self.assertEqual(evidence["sysctl_journal"]["stdout"], "sysctl: exact diagnostic\n")
        self.assertNotIn("activation", fixture.report)
        for call in fixture.execute.call_args_list:
            self.assertEqual(call.kwargs, {"timeout": 5, "check": False})
        self.assertIn("--lines=100", fixture.execute.call_args_list[1].args[0])
        self.assertIn("--cat-config --no-pager", fixture.execute.call_args_list[3].args[0])
        self.assertNotIn("--boot", fixture.execute.call_args_list[4].args[0])
        self.assertIn("--lines=100", fixture.execute.call_args_list[4].args[0])
        self.assertEqual(evidence["sysctl_unit"]["stdout"], "Result=exit-code\n")
        self.assertIn("--identifier=systemd-sysctl", fixture.execute.call_args_list[6].args[0])
        self.assertIn("--priority=warning", fixture.execute.call_args_list[7].args[0])
        self.assertNotIn("--unit", fixture.execute.call_args_list[7].args[0])
        self.assertEqual(fixture.execute.call_args_list[8].args[0], "/pinned/coreutils/bin/cat /proc/sys/kernel/pid_max\n")
        self.assertEqual(evidence["kernel_pid_max"], {"exit_status": 0, "stdout": "32768\n", "stderr": ""})
        self.assertEqual(len(fixture.execute.call_args_list), 9)

    def qa_fixture(self, *, group="users:x:100:\n", named=(2, "", ""), numeric=(2, "", ""), previous=None):
        fixture = msb_smoke.Fixture.__new__(msb_smoke.Fixture)
        fixture.nonce, fixture.spec, fixture.report = "owned", {"coreutils": "/pinned/coreutils"}, {}
        home = "/tmp/owned-client"
        account = f"nix-smoke:x:1000:100::{home}:/run/current-system/sw/bin/nologin\n"
        if previous is not None:
            fixture.report["qa_user"] = previous
        answers = [(0, group, ""), named, numeric]
        if previous is None:
            answers.append((0, "", ""))
        answers.extend([(0, account, ""), (0, account, ""), (0, "1000\n100\n100\n", ""), (0, "", "")])
        fixture.execute = Mock(side_effect=answers)
        return fixture

    def test_qa_user_creation_is_guarded_and_has_exact_ordinary_identity(self):
        fixture = self.qa_fixture()
        fixture.setup_qa_user()
        self.assertEqual(fixture.report["qa_user"], {"name": "nix-smoke", "uid": 1000, "gid": 100, "home": "/tmp/owned-client"})
        calls = fixture.execute.call_args_list
        self.assertEqual(len(calls), 8)
        self.assertIn("group users", calls[0].args[0])
        self.assertIn("passwd nix-smoke", calls[1].args[0])
        self.assertIn("passwd 1000", calls[2].args[0])
        creation = calls[3].args[0]
        self.assertTrue(creation.startswith("test ! -e /tmp/owned-client && test ! -L /tmp/owned-client\n"))
        self.assertIn("/run/current-system/sw/bin/useradd --uid 1000 --gid users --no-create-home --no-user-group", creation)
        self.assertNotIn("--password", creation)
        self.assertEqual(calls[6].kwargs["user"], "1000:100")
        self.assertIn("mkdir --mode=700", calls[7].args[0])

    def test_qa_collisions_lookup_errors_and_wrong_group_cannot_modify_accounts_or_home(self):
        cases = [{"group": "wheel:x:100:\n"}, {"group": "users:x:0:\n"},
                 {"group": "users:x:100:nix-smoke\n"},
                 {"named": (0, "existing", "")}, {"numeric": (0, "existing", "")},
                 {"named": (1, "", "lookup failed")}, {"numeric": (2, "", "unexpected diagnostic")}]
        for case in cases:
            with self.subTest(case=case):
                fixture = self.qa_fixture(**case)
                with self.assertRaises(ValueError):
                    fixture.setup_qa_user()
                self.assertTrue(all("useradd" not in call.args[0] and "mkdir" not in call.args[0]
                                    for call in fixture.execute.call_args_list))

    def test_qa_restart_reuses_only_the_exact_recorded_account(self):
        identity = {"name": "nix-smoke", "uid": 1000, "gid": 100, "home": "/tmp/owned-client"}
        account = "nix-smoke:x:1000:100::/tmp/owned-client:/run/current-system/sw/bin/nologin\n"
        fixture = self.qa_fixture(named=(0, account, ""), numeric=(0, account, ""), previous=identity)
        fixture.setup_qa_user()
        self.assertFalse(any("useradd" in call.args[0] for call in fixture.execute.call_args_list))
        self.assertEqual(fixture.report["qa_user"], identity)
        fixture = self.qa_fixture(previous=identity)
        with self.assertRaisesRegex(ValueError, "missing after restart"):
            fixture.setup_qa_user()
        self.assertFalse(any("useradd" in call.args[0] or "mkdir" in call.args[0] for call in fixture.execute.call_args_list))

    def test_qa_identity_rejects_privileged_groups_wrong_uid_and_lookup_disagreement(self):
        home = "/tmp/owned-client"
        account = f"nix-smoke:x:1000:100::{home}:/run/current-system/sw/bin/nologin\n"
        arguments = [account, account, "users:x:100:\n", "1000\n100\n100\n", home]
        contract.qa_identity(*arguments)
        for index, value in ((0, account.replace(":1000:", ":0:")), (1, account + account),
                             (2, "users:x:100:root\n"), (3, "1000\n100\n100 0\n"),
                             (3, "0\n100\n100\n"), (4, "/tmp/another-home")):
            changed = list(arguments)
            changed[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                contract.qa_identity(*changed)

    def test_disallowed_client_requires_actual_authorization_error(self):
        message = "error: access denied by the Nix daemon (check the `allowed-users` setting in the daemon)\n"
        contract.denied_client(1, '{"url":"daemon"}\n', message)
        for status, stdout, stderr in [(0, '{"url":"daemon"}', message), (1, '{"url":"daemon"}', "connection refused"),
                                       (1, '{"url":"daemon","trusted":false}', message), (1, "", message)]:
            with self.subTest(status=status, stderr=stderr), self.assertRaises(ValueError):
                contract.denied_client(status, stdout, stderr)

    def test_diagnostic_failure_or_oversize_is_not_healthy(self):
        fixture = msb_smoke.Fixture.__new__(msb_smoke.Fixture)
        fixture.spec, fixture.report = {"systemd": "/pinned/systemd", "coreutils": "/pinned/coreutils"}, {}
        fixture.execute = Mock(return_value=(1, "", "bus unavailable"))
        fixture.boot_diagnostics()
        self.assertEqual(fixture.report["boot_diagnostics"][0]["unit_health"], "unavailable")
        fixture.execute = Mock(return_value=(0, "x" * (128 * 1024 + 1), ""))
        with self.assertRaisesRegex(ValueError, "budget"):
            fixture.boot_diagnostics()

    def exec_fixture(self, statuses=("Running", "Running"), pids=((123,), (123,))):
        fixture = msb_smoke.Fixture.__new__(msb_smoke.Fixture)
        fixture.name, fixture.nonce, fixture.spec = "owned", "nonce", {"bash": "/pinned/bash"}
        fixture.supervisor = Mock(pidfds={123: 77})
        fixture.runtime_pids = Mock(side_effect=[list(value) for value in pids])
        answers = [(0, json.dumps({"name": "owned", "status": statuses[0]}), ""),
                   (0, "guest-output", ""),
                   (0, json.dumps({"name": "owned", "status": statuses[1]}), "")]
        fixture.command = Mock(side_effect=answers)
        return fixture

    def test_exec_requires_same_pinned_live_vmm(self):
        fixture = self.exec_fixture()
        with patch("msb_smoke.os.dup", return_value=88), patch("msb_smoke.os.close") as close, patch("msb_smoke.signal.pidfd_send_signal") as alive:
            self.assertEqual(fixture.execute("true\n"), (0, "guest-output", ""))
        self.assertEqual([call.args[0][0] for call in fixture.command.call_args_list], ["inspect", "exec", "inspect"])
        self.assertEqual([call.args for call in alive.call_args_list], [(88, 0), (88, 0)])
        close.assert_called_once_with(88)

    def test_stopped_or_crashed_cannot_trigger_exec_autostart(self):
        for state in ("Stopped", "Crashed", "Draining"):
            fixture = self.exec_fixture(statuses=(state, "Running"))
            with self.subTest(state=state), patch("msb_smoke.os.dup") as duplicate:
                with self.assertRaisesRegex(ValueError, "refusing exec"):
                    fixture.execute("true\n")
                duplicate.assert_not_called()
                self.assertEqual([call.args[0][0] for call in fixture.command.call_args_list], ["inspect"])

    def test_exec_replacement_or_original_pidfd_death_fails(self):
        fixture = self.exec_fixture(pids=((123,), (456,)))
        with patch("msb_smoke.os.dup", return_value=88), patch("msb_smoke.os.close") as close, patch("msb_smoke.signal.pidfd_send_signal"):
            with self.assertRaisesRegex(ValueError, "replaced"):
                fixture.execute("true\n")
        close.assert_called_once_with(88)
        fixture = self.exec_fixture()
        with patch("msb_smoke.os.dup", return_value=88), patch("msb_smoke.os.close") as close, patch("msb_smoke.signal.pidfd_send_signal", side_effect=[None, ProcessLookupError("original died")]):
            with self.assertRaises(ProcessLookupError):
                fixture.execute("true\n")
        close.assert_called_once_with(88)
        fixture = self.exec_fixture(statuses=("Running", "Crashed"))
        with patch("msb_smoke.os.dup", return_value=88), patch("msb_smoke.os.close") as close, patch("msb_smoke.signal.pidfd_send_signal"):
            with self.assertRaisesRegex(ValueError, "refusing exec"):
                fixture.execute("true\n")
        close.assert_called_once_with(88)


if __name__ == "__main__":
    unittest.main()
