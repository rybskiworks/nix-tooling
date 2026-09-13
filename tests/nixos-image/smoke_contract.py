"""Independent bounded receipt predicates for the opt-in native runtime test."""

import json
import os
import re


STORE = re.compile(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9+._?=-]+")
HEX = re.compile(r"[0-9a-f]{64}")
QA_NAME, QA_UID, QA_GID = "nix-smoke", 1000, 100
DEVENV_CACHE = "https://devenv.cachix.org"
DEVENV_KEY = "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
LIX_RUNTIME_REVISION = "251b368a868d578ead123071c3e6bc8eec013817"
FALLBACK = ("flush window elapsed, triggering host exit", "graceful stop exceeded timeout",
            "escalating to kill", "agent relay wait_ready failed",
            "stop_local: agent endpoint unreachable; falling back to process termination")


def require(value, message):
    if not value:
        raise ValueError(message)


def decode(value):
    require(isinstance(value, str) and len(value.encode()) <= 2 * 1024**2, "JSON budget")
    return json.loads(value)


def store_path(value):
    require(isinstance(value, str) and STORE.fullmatch(value), "invalid store root")
    return value


def cache_configuration(settings, *, engine="determinate"):
    """Verify the shared signed cache addition, retaining Nixd-managed defaults."""
    values = {}
    for key in ("substituters", "trusted-substituters", "trusted-public-keys"):
        value = settings.get(key, {}).get("value")
        require(isinstance(value, list) and all(isinstance(item, str) for item in value), "cache settings type")
        values[key] = value
    require(values["substituters"].count(DEVENV_CACHE) == 1, "shared devenv cache missing or duplicated")
    require(values["trusted-public-keys"].count(DEVENV_KEY) == 1, "shared devenv signing key missing or duplicated")
    require(not any(item.startswith("devenv.cachix.org-1:") and item != DEVENV_KEY
                    for item in values["trusted-public-keys"]), "unexpected devenv signing key")
    require(not any(item.rstrip("/") == DEVENV_CACHE for item in values["trusted-substituters"]),
            "devenv cache changed client substitution authority")
    require(engine in {"determinate", "lix"}, "unsupported engine")
    if engine == "lix":
        expected = {
            "substituters": ["https://cache.nixos.org/", DEVENV_CACHE],
            "trusted-substituters": [],
            "trusted-public-keys": ["cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY=", DEVENV_KEY],
        }
        require(all(sorted(values[key]) == sorted(value) for key, value in expected.items()),
                "unexpected Lix cache configuration")
    return values


def isolated_environment(root, msb):
    """No inherited credentials, provider/backend selectors or project state."""
    return {
        "HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_DATA_HOME": str(root / "data"), "XDG_STATE_HOME": str(root / "state"),
        "XDG_RUNTIME_DIR": str(root / "run"),
        "MSB_HOME": str(root / "msb"), "MSB_CONFIG_PATH": str(root / "msb.json"),
        "MSB_BACKEND": "local", "PATH": str(msb.parent) + ":/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "NO_COLOR": "1", "LC_ALL": "C",
    }


def validate_spec(spec):
    require(type(spec.get("version")) is int and spec["version"] in {1, 2}, "unsupported smoke spec")
    fields = {"archive", "toplevel", "engine", "bash", "coreutils", "systemd", "glibc", "utilLinux", "registration"}
    extra = {"version", "roots"}
    if spec["version"] == 1:
        fields.add("nixd")
    else:
        extra.add("engineKind")
        require(spec.get("engineKind") == "lix", "unsupported explicit engine")
    require(set(spec) == fields | extra, "unexpected spec fields")
    for name in fields:
        store_path(spec[name])
    require(isinstance(spec["roots"], list) and len(spec["roots"]) == 2, "base roots")
    for root in spec["roots"]:
        store_path(root)
    require(len(set(spec["roots"])) == 2, "duplicate base roots")
    return spec


def engine_kind(spec):
    """The original version-one fixture keeps its Determinate defaults."""
    return spec.get("engineKind", "determinate")


def validate_mode(spec, mode):
    if engine_kind(spec) == "lix":
        require(mode == "build-persistence", "Lix full acceptance awaits the journal-correlated denial control")
    else:
        require(mode in {"activation", "full", "shutdown-diagnostic"}, "unsupported legacy mode")


def inspect_runtime_contract(spec, revision, version):
    if engine_kind(spec) == "lix":
        require(spec.get("version") == 2 and revision == LIX_RUNTIME_REVISION
                and version == "msb 0.6.18", "unreviewed Lix runtime/spec pair")
        return LIX_RUNTIME_REVISION
    return "legacy"


def daemon_query(spec):
    kind = engine_kind(spec)
    require(kind in {"determinate", "lix"}, "unsupported engine")
    operation = "ping" if kind == "lix" else "info"
    return spec["engine"] + f"/bin/nix store {operation} --store daemon --json"


def restricted_override_warning(spec, setting):
    prefix = "Ignoring" if engine_kind(spec) == "lix" else "ignoring"
    return prefix + " the client-specified setting '" + setting + "'"


def readiness_units(spec):
    kind = engine_kind(spec)
    require(kind in {"determinate", "lix"}, "unsupported engine")
    units = ["guest-store-registration.service", "nix-daemon.socket"]
    return units if kind == "lix" else units + ["determinate-nixd.socket"]


def readiness_script(spec):
    # Multiple arguments to is-active succeed when ANY unit is active.
    return "\n".join(spec["systemd"] + "/bin/systemctl is-active --quiet " + unit
                     for unit in readiness_units(spec))


def validate_daemon_service(spec, service):
    if engine_kind(spec) == "lix":
        # systemctl's structured ExecStart value must select this executable,
        # not merely mention it in another program's arguments.
        require(re.search(r"(?:^|[ {])path=" + re.escape(spec["engine"] + "/bin/nix-daemon")
                          + r" ;", service) is not None, "wrong Lix daemon executable")
        require("argv[]=nix-daemon --daemon ;" in service, "wrong Lix daemon arguments")
    else:
        require(spec["nixd"] + "/bin/determinate-nixd" in service and spec["engine"] + "/bin" in service,
                "wrong daemon pair")


def validate_inspect(record, name, image, *, runtime_revision="legacy"):
    require(runtime_revision in {"legacy", LIX_RUNTIME_REVISION}, "unsupported inspect runtime")
    require(record.get("name") == name and record.get("status") == "Running", "sandbox not running")
    require(record.get("pending_changes") == [], "pending configuration changes")
    for config in (record.get("config"), record.get("active_config")):
        require(isinstance(config, dict), "missing active configuration")
        require(config.get("name") == name, "wrong sandbox identity")
        expected_env = [{"key": "PATH", "value": "/run/current-system/sw/bin"},
                        {"key": "NIX_REMOTE", "value": "daemon"}]
        require(config.get("env") == expected_env, "unexpected image environment")
        require(not config.get("labels") and not config.get("rlimits"), "unexpected labels or process limits")
        # The pinned SDK supplies this guest-only OCI /tmp mount. Exact shape
        # prevents a host source, another mount, or an unreviewed option hiding here.
        expected_mounts = [{"type": "Tmpfs", "guest": "/tmp", "size_mib": 512,
                            "options": {"readonly": False, "nosuid": False,
                                        "nodev": False, "noexec": False}}]
        require(json.dumps(config.get("mounts"), sort_keys=True) == json.dumps(expected_mounts, sort_keys=True)
                and config.get("patches") == [], "unexpected host mount/patch")
        require(not config.get("vsock"), "unexpected host socket")
        require(config.get("init") == {"cmd": "/init", "args": [], "env": [["container", "microsandbox"]]}, "wrong init")
        require(config.get("pull_policy", "").lower() == "never", "registry fallback enabled")
        require(config.get("security_profile", "").lower() == "default", "guest policy changed")
        resources = config.get("resources", {})
        require(resources.get("cpus") == resources.get("max_cpus") == 1, "CPU budget")
        require(resources.get("memory_mib") == resources.get("max_memory_mib") == 2048, "memory budget")
        require(not resources.get("nested_virt", False), "unexpected nesting")
        require(not resources.get("placement_profile"), "unexpected host placement profile")
        runtime = config.get("runtime", {})
        require(runtime.get("workdir") == "/" and not runtime.get("scripts"), "unexpected guest runtime")
        require(not runtime.get("entrypoint") and not runtime.get("cmd"), "unexpected startup command")
        source = config.get("image", {})
        require(set(source) == {"Oci"} and source["Oci"].get("reference") == image, "wrong image source")
        require(source["Oci"].get("root_disk") == {"kind": "managed", "size_mib": 4096}, "root disk budget")
        # --no-net round-trips source defaults into explicit subdocuments.
        # Disabled TLS and an empty secret list are not active capabilities.
        # Whole-shape equality also rejects unknown keys and all new capabilities.
        expected_network = {
            "enabled": True, "interface": {}, "ports": [], "max_connections": None,
            "policy": {"default_egress": "deny", "default_ingress": "deny", "rules": []},
            "dns": {"nameservers": [], "query_timeout_ms": 5000, "rebind_protection": True},
            "secrets": {"secrets": [], "on_violation": "block-and-log"},
            "trust_host_cas": False,
            "tls": {"enabled": False, "intercepted_ports": [443], "bypass": [],
                    "verify_upstream": True, "block_quic_on_intercept": True,
                    "upstream_ca_cert": [], "scoped_upstream_ca_cert": [], "scoped_verify_upstream": [],
                    "intercept_ca": {"cert_path": None, "key_path": None},
                    "cache": {"capacity": 1000, "validity_hours": 24}},
        }
        if runtime_revision == LIX_RUNTIME_REVISION:
            # Native 251 serializes its default hostname-inspection flag.
            # Deny-all policy and every other capability remain exact.
            expected_network["strict"] = False
        require(json.dumps(config.get("network"), sort_keys=True) == json.dumps(expected_network, sort_keys=True),
                "unexpected network capability/default")
        require(config.get("lifecycle", {}).get("max_duration_secs") == 300, "runtime lifetime cap")
        require(not config["lifecycle"].get("ephemeral") and config["lifecycle"].get("idle_timeout_secs") is None, "unexpected lifecycle behavior")
    return record


def qa_identity(account, by_uid, group, ids, home):
    expected = [QA_NAME, "x", str(QA_UID), str(QA_GID), "", home, "/run/current-system/sw/bin/nologin"]
    require(account.strip().split(":") == expected and by_uid == account, "unexpected QA account")
    require(group.strip().split(":") == ["users", "x", str(QA_GID), ""], "unexpected ordinary group")
    require(ids.splitlines() == [str(QA_UID), str(QA_GID), str(QA_GID)], "unexpected QA process identity/groups")
    return {"name": QA_NAME, "uid": QA_UID, "gid": QA_GID, "home": home}


def denied_client(status, stdout, stderr):
    require(type(status) is int and status == 1 and "access denied by the Nix daemon" in stderr and "allowed-users" in stderr,
            "expected disallowed-client rejection")
    require(decode(stdout) == {"url": "daemon"}, "unexpected denied-client response")


def retryable_denial_transport(status, stdout, stderr):
    # The client flushes its greeting before reading the server's denial magic;
    # an early peer close can interrupt that write. This is not denial evidence.
    return (type(status) is int and status == 1 and stdout == '{"url":"daemon"}\n'
            and stderr == "error: cannot open connection to remote store 'daemon': write of 16 bytes: Broken pipe\n")


def mapped_uid(namespace_uid, raw_map, account, submitter=QA_UID):
    require(type(namespace_uid) is int and namespace_uid > 0, "root namespace worker")
    rows = [list(map(int, line.split())) for line in raw_map.splitlines()]
    require(rows and all(len(row) == 3 and min(row) >= 0 and row[2] > 0 for row in rows), "invalid UID map")
    covering = [row for row in rows if row[0] <= namespace_uid < row[0] + row[2]]
    require(len(covering) == 1, "ambiguous UID mapping")
    inside, outside, _ = covering[0]
    uid = outside + namespace_uid - inside
    require(uid not in (0, submitter), "worker has submitting identity")
    fields = account.strip().split(":")
    require(len(fields) == 7 and fields[0].startswith("nixbld") and int(fields[2]) == uid, "worker is not nixbld")
    return uid


def derivation_inputs(payload, expected):
    definitions = payload.get("derivations", payload)
    require(len(definitions) == 1, "ambiguous derivation")
    definition = next(iter(definitions.values()))
    sources = definition["inputs"]["srcs"] if "inputs" in definition else definition["inputSrcs"]
    sources = {path if path.startswith("/nix/store/") else "/nix/store/" + path for path in sources}
    require(sources == set(expected), "unexpected derivation input sources")
    return sources


def validate_stop(stop):
    require(stop.get("cli_status") == 0, "stop command failed")
    require(0 <= stop.get("elapsed", -1) < 8, "host fallback window reached")
    require(stop.get("runtime_exit") == {"code": os.CLD_EXITED, "status": 0}, "runtime did not exit normally")
    require(stop.get("flush_marker") is True, "guest flush marker missing")
    require("Power off not available: System halted instead" not in stop.get("logs", ""),
            "guest poweroff unavailable")
    require(not any(marker in stop.get("logs", "") for marker in FALLBACK), "shutdown fallback observed")
    require(not stop.get("signals"), "supervisor terminated runtime")


def verdict(report):
    require(not report.get("error"), "primary failure")
    require(report.get("activation") is True and report.get("registration") is True, "activation incomplete")
    require(report.get("root_trusted") is True and report.get("qa_trusted") is False, "daemon trust mismatch")
    positive_only = report.get("mode") == "build-persistence"
    if positive_only:
        require(report.get("engine_kind") == "lix" and "nobody_denied" in report
                and report["nobody_denied"] is None and report.get("denied_client_control") == "not_run",
                "missing explicit denied-client coverage limitation")
    else:
        require(report.get("nobody_denied") is True, "daemon trust mismatch")
    require(report.get("cleanup") == {"empty_store": True, "children_empty": True, "forced": False, "errors": []}, "cleanup incomplete")
    if report.get("mode") == "activation":
        return "activation_only_passed"
    if report.get("mode") == "shutdown-diagnostic":
        from shutdown_diagnostic import validate_diagnostic
        require(report.get("builds") == ["ordinary", "untrusted-overrides"]
                and report.get("daemon_restart") is True, "diagnostic pre-stop controls incomplete")
        require(report.get("stops") == [] and not report.get("persistent_store"),
                "diagnostic cannot replace full lifecycle acceptance")
        validate_diagnostic(report.get("shutdown_diagnostic", {}))
        return "guest_poweroff_diagnostic_passed"
    require(report.get("mode") == "full" or positive_only, "unknown mode")
    require(report.get("builds") == ["ordinary", "untrusted-overrides"], "uncached builds incomplete")
    require(report.get("daemon_restart") is True and report.get("persistent_store") is True, "persistence incomplete")
    require(len(report.get("stops", [])) == 2, "normal shutdown count")
    for stop in report["stops"]:
        validate_stop(stop)
        if positive_only:
            require("Powering off." in stop.get("logs", "") and "reboot: Power down" in stop.get("logs", ""),
                    "Lix guest poweroff sequence incomplete")
    health = report.get("boot_diagnostics")
    require(isinstance(health, list) and len(health) == 2
            and all(isinstance(snapshot, dict)
                    and snapshot.get("unit_health") == "no_failed_units_reported"
                    for snapshot in health), "full boot-unit health incomplete")
    for snapshot in health:
        observed = snapshot.get("kernel_pid_max")
        require(isinstance(observed, dict) and type(observed.get("exit_status")) is int
                and observed["exit_status"] == 0 and not observed.get("stderr")
                and isinstance(observed.get("stdout"), str)
                and re.fullmatch(r"[1-9][0-9]{0,9}\n", observed["stdout"]) is not None,
                "guest kernel PID range was not observed")
    return "build_persistence_passed" if positive_only else "full_acceptance_passed"
