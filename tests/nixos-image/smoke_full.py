"""Full uncached-build and lifecycle stages, separate from activation diagnostics."""

import base64
import json
import os
import shlex
import time

from smoke_contract import QA_GID, QA_NAME, QA_UID, cache_configuration, decode, derivation_inputs, mapped_uid, require, store_path, validate_stop


def log_text(raw, *, source=None):
    chunks = []
    for line in raw.splitlines():
        entry = decode(line)
        require(entry.get("s") in {"stdout", "stderr", "output", "system", "boot-error"}, "unknown log source")
        body = entry.get("d")
        require(isinstance(body, str), "invalid log body")
        if entry.get("e") == "b64":
            body = base64.b64decode(body, validate=True).decode("utf-8", errors="replace")
        else:
            require(entry.get("e") is None, "unknown log encoding")
        if source is None or entry["s"] == source:
            chunks.append(body)
    return "\n".join(chunks)


def expression(spec, nonce):
    # toFile retains the coreutils string context in the builder's own closure.
    return f'''let
  bash = builtins.storePath {json.dumps(spec['bash'])};
  utils = builtins.storePath {json.dumps(spec['coreutils'])};
  declared = builtins.toFile "declared-{nonce}" "declared-input\\n";
  script = builtins.toFile "builder-{nonce}" ''
    set -eu
    test ! -e /etc/nixos-smoke-outside
    test "$(${{utils}}/bin/id -u)" != 0
    if (exec 3<>/dev/tcp/127.0.0.1/19877); then
      echo 'sandbox reached the outside listener' >&2
      exit 1
    fi
    ${{utils}}/bin/mkdir "$out"
    ${{utils}}/bin/cat "$declared" > "$out/value"
    ${{utils}}/bin/id -u > "$out/worker-uid"
    ${{utils}}/bin/cat /proc/self/uid_map > "$out/worker-uid-map"
  '';
in {{
  inputs = map toString [ bash script declared ];
  drv = builtins.derivation {{
    name = "nixos-msb-{nonce}";
    system = "x86_64-linux";
    builder = bash + "/bin/bash";
    args = [ "-e" script ];
    inherit declared;
  }};
}}
'''


def build_account(fixture, uid):
    require(type(uid) is int and 0 < uid < 2**32, "invalid build account UID")
    # NixOS exposes getent separately from glibc's generic bin output.
    return fixture.execute(f"/run/current-system/sw/bin/getent passwd {uid}\n")[1]


def arm_flush(fixture, boot):
    s, c = fixture.spec, fixture.spec["coreutils"] + "/bin/"
    marker = f"NIXOS_FLUSH:{fixture.nonce}:{boot}"
    script = f"""set -eu
printf '%s\\n' {shlex.quote(marker)} > /var/lib/nixos-smoke-flush
{c}sync -f /var/lib/nixos-smoke-flush
printf '%s\\n' {shlex.quote(marker)} > /dev/console
"""
    path = f"/tmp/{fixture.nonce}-flush.sh"
    fixture.execute(f"{c}cat > {path} <<'SMOKE_FLUSH'\n{script}SMOKE_FLUSH\n"
                    f"{s['systemd']}/bin/systemd-run --unit={fixture.nonce}-flush --property=Type=oneshot "
                    f"--property=RemainAfterExit=yes --property=ExecStop='{s['bash']}/bin/bash {path}' {c}true\n"
                    f"{s['systemd']}/bin/systemctl is-active --quiet {fixture.nonce}-flush.service\n")
    return marker


def observe_vmm_exit(fixture, pid, fd, started, deadline):
    """Observe the pinned child after CLI completion without signalling it."""
    while True:
        fixture.supervisor.refresh()
        events = [event for event in fixture.supervisor.exit_events
                  if event["pid"] == pid and event["observed"] >= started]
        require(len(events) <= 1, "ambiguous observed VMM exit")
        if events:
            return {key: events[0][key] for key in ("code", "status")}
        try:
            status = os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            status = None  # A subsequent refresh must retain the original exit.
        if status is not None:
            require(status.si_pid == pid, "unexpected pinned VMM exit identity")
            return {"code": status.si_code, "status": status.si_status}
        fixture.limits(deadline)
        time.sleep(0.02)


def stop(fixture, marker):
    from shutdown_diagnostic import snapshot
    record = {"runtime_exit": None}
    fixture.report["stops"].append(record)
    # Use the same read-only pre-stop evidence for both request paths. This is
    # diagnostic work, not a settling sleep or a change to the stop deadline.
    snapshot(fixture, record)
    pids = fixture.runtime_pids()
    require(len(pids) == 1, "VMM identity lost before stop")
    fd = os.dup(fixture.supervisor.pidfds[pids[0]])
    previous_signals = len(fixture.supervisor.signal_events)
    record["runtime_pid"] = pids[0]
    try:
        require(os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None,
                "VMM already exited before stop")
        started = time.monotonic()
        status, _, stderr = fixture.command(["stop", fixture.name, "--timeout", "10"], timeout=15, check=False)
        record["cli_status"] = status
        record["cli_elapsed"] = time.monotonic() - started
        observation_error = None
        try:
            record["runtime_exit"] = observe_vmm_exit(
                fixture, pids[0], fd, started, min(fixture.deadline, started + 10))
        except InterruptedError:
            raise
        except Exception as error:
            observation_error = error
            record["observation_error"] = repr(error)
        # The strict deadline includes observing the exit, not just CLI return.
        record["elapsed"] = time.monotonic() - started
        log_error = None
        try:
            raw_logs = fixture.capture_logs()
            record["logs"] = log_text(raw_logs) + stderr
            record["flush_marker"] = marker in log_text(raw_logs, source="system").splitlines()
        except InterruptedError:
            raise
        except Exception as error:
            log_error = error
            record["log_error"] = repr(error)
        record["signals"] = fixture.supervisor.signal_events[previous_signals:]
        if observation_error is not None:
            raise observation_error
        if log_error is not None:
            raise log_error
        validate_stop(record)
        require(not fixture.runtime_pids(), "VMM survived stop")
    finally:
        record["signals"] = fixture.supervisor.signal_events[previous_signals:]
        os.close(fd)


def prepare_full_acceptance(fixture):
    """Run the common first-boot controls before either shutdown observation."""
    s, c = fixture.spec, fixture.spec["coreutils"] + "/bin/"
    nix = s["engine"] + "/bin/"
    systemctl = s["systemd"] + "/bin/systemctl"
    user = f"{QA_UID}:{QA_GID}"
    fixture.execute(f"printf 'outside-positive\\n' > /etc/nixos-smoke-outside\n"
                    f"{s['systemd']}/bin/systemd-run --unit={fixture.nonce}-listener --property=RuntimeMaxSec=240 "
                    f"{s['systemd']}/bin/systemd-socket-activate --accept --inetd -l 127.0.0.1:19877 {c}cat\n")
    deadline = min(fixture.deadline, time.monotonic() + 10)
    while True:
        status, _, _ = fixture.execute("exec 3<>/dev/tcp/127.0.0.1/19877\n", user=user, timeout=2, check=False)
        if status == 0:
            break
        require(time.monotonic() < deadline, "outside listener positive control")
        time.sleep(0.1)
    require(fixture.execute(c + "cat /etc/nixos-smoke-outside\n", user=user)[1] == "outside-positive\n", "outside file control")
    settings = fixture.guest_json(nix + "nix config show --json", user=user)
    require(settings["sandbox"]["value"] is True and settings["sandbox-fallback"]["value"] is False, "sandbox configuration")
    require(settings["require-sigs"]["value"] is True and set(settings["trusted-users"]["value"]) == {"root"}, "daemon trust configuration")
    fixture.report["configured_caches"] = cache_configuration(settings)
    fixture.report["substitution_policy"] = "per-request substitute=false; configured supplier defaults retained"
    outputs = []
    fixture.execute(c + "mkdir -p /var/lib/nixos-smoke\n")
    fixture.report["builds"] = []
    fixture.report["build_evidence"] = []
    for kind in ("ordinary", "untrusted-overrides"):
        name = fixture.nonce + "-" + kind
        file = f"/var/lib/nixos-smoke/{name}.nix"
        fixture.execute(f"{c}cat > {file} <<'SMOKE_NIX'\n{expression(s, name)}SMOKE_NIX\n")
        inputs = fixture.guest_json(f"{nix}nix-instantiate --eval --strict --json {file} --attr inputs", user=user)
        require(len(inputs) == 3 and inputs[0] == s["bash"], "expected declared input triple")
        for item in inputs:
            store_path(item)
        drv = fixture.execute(f"{nix}nix-instantiate {file} --attr drv\n", user=user)[1].strip()
        store_path(drv)
        definition = fixture.guest_json(f"{nix}nix derivation show {drv}", user=user)
        derivation_inputs(definition, inputs)
        out = fixture.execute(f"{nix}nix-store --query --outputs {drv}\n", user=user)[1].strip()
        store_path(out)
        fixture.execute(f"test ! -e {out}\n")
        fixture.execute("exec 3<>/dev/tcp/127.0.0.1/19877\n", user=user, timeout=2)
        options = " --option substitute false"
        if kind == "untrusted-overrides":
            options += f" --option sandbox false --option require-sigs false --option trusted-users {QA_NAME}"
        status, stdout, stderr = fixture.execute(f"{nix}nix-build {file} --attr drv --no-out-link {options}\n", user=user, timeout=45, check=False)
        evidence = {"kind": kind, "drv": drv, "inputs": inputs, "output": out, "exit_status": status, "stderr": stderr}
        fixture.report["build_evidence"].append(evidence)
        require(status == 0 and stdout.strip() == out, "uncached guest build failed")
        require(fixture.execute(f"{c}cat {out}/value\n")[1] == "declared-input\n", "built value")
        uid = int(fixture.execute(f"{c}cat {out}/worker-uid\n")[1].strip())
        mapping = fixture.execute(f"{c}cat {out}/worker-uid-map\n")[1]
        # Determine the mapped UID before looking up its actual NixOS account.
        rows = [list(map(int, line.split())) for line in mapping.splitlines()]
        require(all(len(row) == 3 for row in rows), "invalid UID map shape")
        covered = [row for row in rows if row[0] <= uid < row[0] + row[2]]
        require(len(covered) == 1, "ambiguous UID map")
        guest_uid = covered[0][1] + uid - covered[0][0]
        require(0 < guest_uid < 2**32, "invalid mapped UID")
        account = build_account(fixture, guest_uid)
        evidence["mapped_nixbld_uid"] = mapped_uid(uid, mapping, account, submitter=QA_UID)
        evidence["namespace_uid"] = uid
        evidence["uid_map"] = mapping
        if kind == "untrusted-overrides":
            for setting in ("sandbox", "require-sigs"):
                require("ignoring the client-specified setting '" + setting + "'" in stderr, "restricted override warning missing")
            require(fixture.guest_json(nix + f"nix store info --store daemon --json --option trusted-users {QA_NAME}", user=user)["trusted"] is False, "client escalated trust")
        fixture.execute(f"{c}ln -s {out} /nix/var/nix/gcroots/{name}\n")
        fixture.report["builds"].append(kind)
        outputs.append((file, out, name))
    fixture.execute(systemctl + " restart nix-daemon.service\n")
    require(fixture.guest_json(nix + "nix store info --store daemon --json", user=user)["trusted"] is False, "daemon restart trust")
    for _, out, _ in outputs:
        fixture.execute(f"{nix}nix-store --verify-path {out}\n", user=user)
    fixture.report["daemon_restart"] = True
    boot = fixture.execute(c + "cat /proc/sys/kernel/random/boot_id\n")[1].strip()
    fixture.execute(f"printf '%s\\n' {shlex.quote(boot)} > /run/{fixture.nonce}-volatile\n")
    marker = arm_flush(fixture, boot)
    return outputs, boot, marker


def full_acceptance(fixture):
    outputs, boot, marker = prepare_full_acceptance(fixture)
    s, c = fixture.spec, fixture.spec["coreutils"] + "/bin/"
    nix = s["engine"] + "/bin/"
    user = f"{QA_UID}:{QA_GID}"
    stop(fixture, marker)
    fixture.command(["start", fixture.name], timeout=90)
    fixture.activation()
    fixture.report["configured_caches_after_restart"] = cache_configuration(
        fixture.guest_json(nix + "nix config show --json", user=user))
    require(fixture.report["configured_caches_after_restart"] == fixture.report["configured_caches"],
            "cache configuration changed after restart")
    new_boot = fixture.execute(c + "cat /proc/sys/kernel/random/boot_id\n")[1].strip()
    require(new_boot != boot, "same guest boot identity")
    fixture.execute(f"test ! -e /run/{fixture.nonce}-volatile\n")
    require(fixture.execute(c + "cat /var/lib/nixos-smoke-flush\n")[1] == marker + "\n", "flush marker not persistent")
    for file, out, name in outputs:
        fixture.execute(f"{nix}nix-store --verify-path {out}\ntest -L /nix/var/nix/gcroots/{name}\n", user=user)
        _, stdout, stderr = fixture.execute(f"{nix}nix-build {file} --attr drv --no-out-link --option substitute false\n", user=user, timeout=15)
        actual = stdout.strip()
        require(actual == out, "persistent derivation changed")
        require("building '" not in stderr and "will be built" not in stderr, "unexpected rebuild after restart")
    fixture.report["persistent_store"] = True
    stop(fixture, arm_flush(fixture, new_boot))
