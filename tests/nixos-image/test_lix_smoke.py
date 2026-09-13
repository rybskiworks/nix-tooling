"""Engine-specific native fixture controls; never start a daemon or a VM."""

import copy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

import msb_smoke
import smoke_contract as contract


def spec(kind="lix"):
    fields = ("archive", "toplevel", "engine", "bash", "coreutils", "systemd", "glibc", "utilLinux", "registration")
    result = {field: "/nix/store/" + "0" * 32 + "-" + field for field in fields}
    result["roots"] = [result["toplevel"], result["coreutils"]]
    if kind == "lix":
        result.update(version=2, engineKind="lix")
    else:
        result.update(version=1, nixd="/nix/store/" + "0" * 32 + "-nixd")
    return result


class LixSmokeTests(unittest.TestCase):
    def stopped_fixture(self):
        from test_smoke_contract import valid_stop
        fixture = object.__new__(msb_smoke.Fixture)
        fixture.launch_generation = 2
        fixture.normal_stop_witness = None
        fixture.report = {"stops": [dict(valid_stop(), runtime_pid=123)]}
        fixture.supervisor = Mock()
        fixture.supervisor.snapshot.return_value = []
        fixture.record_normal_stop(fixture.report["stops"][-1])
        return fixture

    def test_current_validated_stop_with_fresh_empty_supervision_skips_repeat(self):
        fixture = self.stopped_fixture()
        self.assertTrue(fixture.has_current_normal_stop())
        self.assertEqual(fixture.supervisor.snapshot.call_count, 2)

    def test_unknown_old_mutated_or_live_stop_never_skips(self):
        for change in ("unknown", "missing", "next_generation", "new_stop", "changed_pid", "live"):
            fixture = self.stopped_fixture()
            if change == "unknown":
                fixture.normal_stop_witness = None
            elif change == "missing":
                del fixture.normal_stop_witness
            elif change == "next_generation":
                fixture.launch_generation += 1
            elif change == "new_stop":
                fixture.report["stops"].append({})
            elif change == "changed_pid":
                fixture.report["stops"][-1]["runtime_pid"] = 456
            else:
                fixture.supervisor.snapshot.return_value = [{"pid": 456}]
            with self.subTest(change=change):
                self.assertFalse(fixture.has_current_normal_stop())

    def test_incomplete_failed_or_forced_stop_cannot_supply_witness(self):
        for change in ({"runtime_exit": None}, {"cli_status": 1}, {"flush_marker": False},
                       {"signals": ["TERM"]}, {"elapsed": 8}, {"runtime_pid": None}):
            fixture = self.stopped_fixture()
            fixture.normal_stop_witness = None
            fixture.report["stops"][-1].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                fixture.record_normal_stop(fixture.report["stops"][-1])
            self.assertIsNone(fixture.normal_stop_witness)

    def test_new_launch_attempt_invalidates_witness_before_any_spawn(self):
        for operation in ("create", "start"):
            fixture = self.stopped_fixture()
            # Stop at the first unrelated fixture prerequisite, before any IO.
            with self.subTest(operation=operation), self.assertRaises(AttributeError):
                fixture.command([operation, "owned"])
            self.assertEqual(fixture.launch_generation, 3)
            self.assertIsNone(fixture.normal_stop_witness)

    def test_cleanup_still_requests_stop_for_unknown_active_or_invalid_witness(self):
        for state in ("confirmed", "unknown", "active", "invalid", "inspection_error"):
            fixture = self.stopped_fixture()
            fixture.name, fixture.attempted = "owned", True
            fixture.capture_logs = Mock(return_value="")
            fixture.supervisor.signal_events = []
            fixture.supervisor.exit_events = []
            fixture.supervisor.children.return_value = []
            fixture.command = Mock(side_effect=lambda args, **kwargs: (0, "[]" if args[0] == "list" else "", ""))
            if state == "unknown":
                fixture.normal_stop_witness = None
            elif state == "active":
                fixture.supervisor.snapshot.return_value = [{"pid": 456}]
            elif state == "invalid":
                fixture.report["stops"][-1]["flush_marker"] = False
            elif state == "inspection_error":
                fixture.supervisor.snapshot.side_effect = RuntimeError("unavailable")
            fixture.cleanup()
            operations = [call.args[0][0] for call in fixture.command.call_args_list]
            with self.subTest(state=state):
                self.assertEqual("stop" not in operations, state == "confirmed")
                self.assertIn("remove", operations)
                self.assertEqual(fixture.report["cleanup"]["errors"] == [], state not in {"invalid", "inspection_error"})

    def test_current_inspect_requires_exact_lix_spec_runtime_relationship(self):
        self.assertEqual(contract.inspect_runtime_contract(spec(), contract.LIX_RUNTIME_REVISION, "msb 0.6.18"),
                         contract.LIX_RUNTIME_REVISION)
        for changed, revision, version in ((dict(spec(), version=1), contract.LIX_RUNTIME_REVISION, "msb 0.6.18"),
                                           (spec(), "0" * 40, "msb 0.6.18"),
                                           (spec(), contract.LIX_RUNTIME_REVISION, "msb 0.6.16")):
            with self.assertRaises(ValueError):
                contract.inspect_runtime_contract(changed, revision, version)
        self.assertEqual(contract.inspect_runtime_contract(spec("determinate"), "older", "msb 0.6.16"), "legacy")

    def test_current_inspect_admits_only_explicit_false_and_no_extra_capabilities(self):
        from test_smoke_contract import inspected
        value = inspected()
        name, image = value["name"], value["config"]["image"]["Oci"]["reference"]
        for key in ("config", "active_config"):
            value[key]["network"]["strict"] = False
        contract.validate_inspect(value, name, image, runtime_revision=contract.LIX_RUNTIME_REVISION)
        with self.assertRaises(ValueError):
            contract.validate_inspect(value, name, image)
        for key in ("config", "active_config"):
            for strict in (True, 0, None, "false", []):
                changed = copy.deepcopy(value)
                changed[key]["network"]["strict"] = strict
                with self.subTest(key=key, value=strict), self.assertRaises(ValueError):
                    contract.validate_inspect(changed, name, image, runtime_revision=contract.LIX_RUNTIME_REVISION)
            for change in ("missing", "extra", "egress"):
                changed = copy.deepcopy(value)
                network = changed[key]["network"]
                if change == "missing":
                    del network["strict"]
                elif change == "extra":
                    network["another_capability"] = False
                else:
                    network["policy"]["default_egress"] = "allow"
                with self.subTest(key=key, change=change), self.assertRaises(ValueError):
                    contract.validate_inspect(changed, name, image, runtime_revision=contract.LIX_RUNTIME_REVISION)
        with self.assertRaises(ValueError):
            contract.validate_inspect(value, name, image, runtime_revision="future")

    def test_lix_resource_floors_do_not_change_legacy_defaults(self):
        gib = msb_smoke.GIB
        self.assertEqual(msb_smoke.resource_policy(), {
            "disk_admission_bytes": 22 * gib, "disk_floor_bytes": 17 * gib,
            "memory_floor_bytes": 6 * gib})
        self.assertEqual(msb_smoke.resource_policy("lix"), {
            "disk_admission_bytes": 25 * gib, "disk_floor_bytes": 20 * gib,
            "memory_floor_bytes": 8 * gib})
        msb_smoke.resource_admission(25 * gib, 8 * gib, engine="lix")
        for free, memory in ((25 * gib - 1, 8 * gib), (25 * gib, 8 * gib - 1)):
            with self.assertRaises(ValueError):
                msb_smoke.resource_admission(free, memory, engine="lix")

    def test_explicit_lix_and_legacy_specs_keep_separate_engines(self):
        for kind, command in (("lix", "ping"), ("determinate", "info")):
            value = spec(kind)
            self.assertEqual(contract.validate_spec(value), value)
            self.assertEqual(contract.engine_kind(value), kind)
            self.assertEqual(contract.daemon_query(value), value["engine"] + f"/bin/nix store {command} --store daemon --json")

    def test_spec_refuses_unknown_or_mixed_engine_contracts(self):
        mutations = [dict(version=True), dict(version=3), dict(engineKind="determinate"),
                     dict(engineKind="future"), dict(nixd=spec("determinate")["nixd"]),
                     dict(engine="/tmp/not-an-engine"), dict(extra=True)]
        for mutation in mutations:
            value = spec()
            value.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                contract.validate_spec(value)
        for key in ("engineKind", "engine", "roots"):
            value = spec()
            del value[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                contract.validate_spec(value)
        legacy = spec("determinate")
        legacy["engineKind"] = "lix"
        with self.assertRaises(ValueError):
            contract.validate_spec(legacy)

    def test_lix_readiness_has_no_nixd_and_requires_every_individual_unit(self):
        for kind in ("lix", "determinate"):
            value = spec(kind)
            units = contract.readiness_units(value)
            self.assertEqual("determinate-nixd.socket" in units, kind == "determinate")
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "bin").mkdir()
                executable = root / "bin/systemctl"
                shell = shutil.which("sh")
                self.assertIsNotNone(shell)
                executable.write_text(f"#!{shell}\n" + "[ \"$#\" -eq 3 ] || exit 2\n"
                                      "[ \"$1 $2\" = 'is-active --quiet' ] || exit 2\n"
                                      "[ \"$3\" != \"$INACTIVE\" ]\n")
                executable.chmod(0o700)
                value["systemd"] = str(root)
                script = contract.readiness_script(value)
                for inactive in ("none", *units):
                    result = subprocess.run([shell, "-e", "-c", script],
                                            env={"INACTIVE": inactive}, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode == 0, inactive == "none")

    def test_lix_daemon_execstart_requires_exact_program_and_arguments(self):
        value = spec()
        engine = value["engine"]
        service = "{ path=" + engine + "/bin/nix-daemon ; argv[]=nix-daemon --daemon ; ignore_errors=no ; }"
        contract.validate_daemon_service(value, service)
        for changed in (service.replace("path=" + engine, "path=/other"),
                        service.replace("/bin/nix-daemon ;", "/bin/nix-daemon-extra ;"),
                        service.replace("argv[]=nix-daemon --daemon ;", "argv[]=nix-daemon --version ;"),
                        "{ path=/other ; argv[]=" + engine + "/bin/nix-daemon ; }"):
            with self.subTest(service=changed), self.assertRaises(ValueError):
                contract.validate_daemon_service(value, changed)

    def test_actual_trust_values_and_lix_template_mask_are_required(self):
        value = spec()
        for trusted in (False, True, None):
            fixture = object.__new__(msb_smoke.Fixture)
            fixture.spec, fixture.report = value, {}
            fixture.guest_json = Mock(side_effect=[{"trusted": True}, {"trusted": trusted}])
            service = "{ path=" + value["engine"] + "/bin/nix-daemon ; argv[]=nix-daemon --daemon ; }"
            fixture.execute = Mock(return_value=(0, service, ""))
            if trusted is False:
                fixture.check_daemon_trust()
                self.assertIn("readlink -f /etc/systemd/system/nix-daemon@.service", fixture.execute.call_args.args[0])
            else:
                with self.assertRaises(ValueError):
                    fixture.check_daemon_trust()
            self.assertTrue(all(" store ping " in call.args[0] for call in fixture.guest_json.call_args_list))

    def test_lix_cache_contract_is_exclusive_not_nixd_managed(self):
        settings = {
            "substituters": {"value": ["https://cache.nixos.org/", contract.DEVENV_CACHE]},
            "trusted-substituters": {"value": []},
            "trusted-public-keys": {"value": ["cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY=", contract.DEVENV_KEY]},
        }
        contract.cache_configuration(settings, engine="lix")
        for key, extra in (("substituters", "https://install.determinate.systems/"),
                           ("trusted-substituters", "https://other.invalid"),
                           ("trusted-public-keys", "unexpected-key")):
            changed = copy.deepcopy(settings)
            changed[key]["value"].append(extra)
            with self.subTest(key=key), self.assertRaises(ValueError):
                contract.cache_configuration(changed, engine="lix")

    def test_modes_never_claim_unsupported_lix_denial_or_full_acceptance(self):
        contract.validate_mode(spec(), "build-persistence")
        for mode in ("activation", "full", "shutdown-diagnostic", "unknown"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                contract.validate_mode(spec(), mode)
        for mode in ("activation", "full", "shutdown-diagnostic"):
            contract.validate_mode(spec("determinate"), mode)
        with self.assertRaises(ValueError):
            contract.validate_mode(spec("determinate"), "build-persistence")

    def test_lix_warning_spelling_is_not_the_determinate_predicate(self):
        self.assertEqual(contract.restricted_override_warning(spec(), "sandbox"),
                         "Ignoring the client-specified setting 'sandbox'")
        self.assertEqual(contract.restricted_override_warning(spec("determinate"), "sandbox"),
                         "ignoring the client-specified setting 'sandbox'")

    def test_positive_verdict_requires_every_build_lifecycle_gate_and_explicit_missing_negative(self):
        # Reuse the established complete normal-stop/boot evidence shape.
        from test_smoke_contract import valid_stop
        report = {"mode": "build-persistence", "engine_kind": "lix", "activation": True,
                  "registration": True, "root_trusted": True, "qa_trusted": False,
                  "nobody_denied": None, "denied_client_control": "not_run",
                  "cleanup": {"empty_store": True, "children_empty": True, "forced": False, "errors": []},
                  "builds": ["ordinary", "untrusted-overrides"], "daemon_restart": True,
                  "persistent_store": True,
                  "stops": [dict(valid_stop(), logs="Powering off.\nreboot: Power down\n") for _ in range(2)],
                  "boot_diagnostics": [{"unit_health": "no_failed_units_reported",
                                        "kernel_pid_max": {"exit_status": 0, "stdout": "4194304\n", "stderr": ""}}] * 2}
        self.assertEqual(contract.verdict(report), "build_persistence_passed")
        changes = [{"mode": "full"}, {"engine_kind": "determinate"}, {"denied_client_control": "passed"},
                   {"nobody_denied": True}, {"persistent_store": False}, {"daemon_restart": False},
                   {"builds": ["ordinary"]}, {"stops": [valid_stop()]},
                   {"stops": [valid_stop(), valid_stop()]}, {"boot_diagnostics": []}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                contract.verdict(dict(report, **change))
        del report["nobody_denied"]
        with self.assertRaises(ValueError):
            contract.verdict(report)


if __name__ == "__main__":
    unittest.main()
