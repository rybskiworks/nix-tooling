"""Explicit opt-in Microsandbox test of an already-built common NixOS image."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from children import ChildSupervisor
from smoke_contract import (HEX, QA_GID, QA_NAME, QA_UID, decode, denied_client, isolated_environment,
                            qa_identity, require, retryable_denial_transport, validate_inspect, validate_spec, verdict)


GIB = 1024**3
DISK_ADMISSION = 22 * GIB
DISK_FLOOR = 17 * GIB
SCRATCH_LIMIT = 4 * GIB
GROWTH_LIMIT = 4 * GIB


def resource_admission(free, memory):
    require(free >= DISK_ADMISSION, "admission requires 22 GiB")
    require(memory >= 8 * GIB, "admission requires 8 GiB available host memory")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def available_memory(text=None):
    if text is None:
        text = Path("/proc/meminfo").read_text()
    rows = [line.split() for line in text.splitlines() if line.startswith("MemAvailable:")]
    require(len(rows) == 1 and len(rows[0]) == 3 and rows[0][2] == "kB", "missing host memory accounting")
    value = int(rows[0][1]) * 1024
    require(value > 0, "invalid host memory accounting")
    return value


def unpack_archive(source, destination, limits, maximum=2 * GIB):
    """MSB's importer expects plain tar; never unpack members on the host here."""
    total, hasher = 0, hashlib.sha256()
    with gzip.open(source, "rb") as compressed, destination.open("xb") as output:
        while True:
            limits()
            block = compressed.read(1024**2)
            if not block:
                break  # Reaching gzip EOF also validates its CRC and size.
            total += len(block)
            require(total <= maximum, "decompressed tar exceeds budget")
            output.write(block)
            hasher.update(block)
    limits()
    return {"path": str(destination), "bytes": total, "sha256": hasher.hexdigest()}


class Fixture:
    def __init__(self, args):
        self.args = args
        self.spec = validate_spec(decode(args.spec.read_text()))
        self.msb = args.msb.resolve(strict=True)
        require(str(self.msb).startswith("/nix/store/") and self.msb.name == "msb", "runtime must be an installed store binary")
        for value in (args.msb_sha256, args.agentd_sha256, args.archive_sha256):
            require(HEX.fullmatch(value), "invalid expected hash")
        require(re.fullmatch(r"[0-9a-f]{40}", args.runtime_revision), "invalid runtime revision")
        self.agentd = self.msb.parent.parent / "libexec/agentd"
        require(digest(self.msb) == args.msb_sha256, "runtime hash mismatch")
        require(digest(self.agentd) == args.agentd_sha256, "agentd hash mismatch")
        require(digest(Path(self.spec["archive"])) == args.archive_sha256, "archive hash mismatch")
        admitted_free = shutil.disk_usage(args.scratch).free
        resource_admission(admitted_free, available_memory())
        require(os.access("/dev/kvm", os.R_OK | os.W_OK), "KVM unavailable")
        self.root = Path(tempfile.mkdtemp(prefix="ng.", dir=args.scratch)).resolve()
        self.root.chmod(0o700)
        self.env = isolated_environment(self.root, self.msb)
        for name in ("home", "tmp", "config", "cache", "data", "state", "run", "msb", "cwd", "logs"):
            (self.root / name).mkdir(mode=0o700)
        (self.root / "msb.json").write_text("{}\n")
        self.nonce = self.root.name.replace(".", "-")
        self.name = "nixos-" + self.nonce
        self.image = "localhost:9/nixos-smoke:" + self.nonce
        self.started = time.monotonic()
        self.deadline = self.started + 300
        self.initial_free = admitted_free
        self.next_scan = 0
        self.supervisor = ChildSupervisor()
        self.sequence = 0
        self.attempted = False
        self.logs = []
        self.report = {
            "version": 1, "status": "running", "mode": args.mode, "root": str(self.root),
            "runtime_revision_asserted_by_caller": args.runtime_revision,
            "msb": str(self.msb), "msb_sha256": args.msb_sha256,
            "agentd": str(self.agentd), "agentd_sha256": args.agentd_sha256,
            "archive": self.spec["archive"], "archive_sha256": args.archive_sha256,
            "stages": [], "stops": [], "full_acceptance": False,
            "initial_free_bytes": self.initial_free,
            "limits": {"disk_admission_bytes": DISK_ADMISSION, "disk_floor_bytes": DISK_FLOOR,
                       "scratch_bytes": SCRATCH_LIMIT, "global_growth_bytes": GROWTH_LIMIT,
                       "memory_admission_bytes": 8 * GIB, "memory_floor_bytes": 6 * GIB,
                       "work_seconds": 300, "cleanup_seconds": 90},
        }

    def limits(self, deadline=None):
        deadline = self.deadline if deadline is None else deadline
        require(time.monotonic() < deadline, "fixture deadline")
        free = shutil.disk_usage(self.root).free
        growth = max(0, self.initial_free - free)
        self.report["minimum_free_bytes"] = min(free, self.report.get("minimum_free_bytes", free))
        self.report["maximum_sampled_global_growth_bytes"] = max(
            growth, self.report.get("maximum_sampled_global_growth_bytes", 0))
        require(free >= DISK_FLOOR, "17 GiB disk floor")
        require(growth <= GROWTH_LIMIT, "4 GiB global disk growth limit")
        memory = available_memory()
        require(memory >= 6 * GIB, "6 GiB available host memory floor")
        self.report["minimum_memory_available_bytes"] = min(memory, self.report.get("minimum_memory_available_bytes", memory))
        # Imported root files can be numerous. Check cheap limits frequently,
        # but walk only the owned tree every five seconds and bound the walk.
        if time.monotonic() >= self.next_scan:
            allocated = 0
            for directory, dirs, files in os.walk(self.root, followlinks=False):
                require(time.monotonic() < deadline, "scratch scan deadline")
                for index, name in enumerate(dirs + files):
                    if index % 256 == 0:
                        require(time.monotonic() < deadline, "scratch scan deadline")
                    try:
                        allocated += (Path(directory) / name).lstat().st_blocks * 512
                    except FileNotFoundError:
                        pass
                    self.report["peak_sampled_scratch_bytes"] = max(
                        allocated, self.report.get("peak_sampled_scratch_bytes", 0))
                    require(allocated <= SCRATCH_LIMIT, "4 GiB scratch quota")
            self.next_scan = time.monotonic() + 5
            self.report["peak_sampled_scratch_bytes"] = max(allocated, self.report.get("peak_sampled_scratch_bytes", 0))
        sizes = [path.stat().st_size for path in self.logs]
        require(not sizes or max(sizes) <= 4 * 1024**2, "per-log quota")
        require(sum(sizes) <= 16 * 1024**2, "total log quota")

    def command(self, arguments, *, timeout=20, stdin=None, check=True, cleanup_deadline=None):
        self.sequence += 1
        prefix = self.root / "logs" / f"{self.sequence:03d}-{arguments[0]}"
        out, err = prefix.with_suffix(".stdout"), prefix.with_suffix(".stderr")
        self.logs.extend([out, err])
        limit = self.deadline if cleanup_deadline is None else cleanup_deadline
        deadline = min(limit, time.monotonic() + timeout)
        require(time.monotonic() < deadline, "command admission deadline")
        record = {"argv": [str(self.msb), *arguments], "stdout": str(out), "stderr": str(err)}
        self.report["stages"].append(record)
        input_path = prefix.with_suffix(".stdin")
        if stdin is not None:
            require(len(stdin.encode()) <= 1024**2, "stdin quota")
            input_path.write_text(stdin)
        with out.open("xb") as stdout, err.open("xb") as stderr:
            source = input_path.open("rb") if stdin is not None else None
            try:
                if cleanup_deadline is None:
                    self.limits(deadline)
                child = self.supervisor.start(record["argv"], cwd=self.root / "cwd", env=self.env,
                                              stdin=source if source is not None else subprocess.DEVNULL,
                                              stdout=stdout, stderr=stderr, start_new_session=True)
                while child.poll() is None:
                    if cleanup_deadline is None:
                        self.limits(deadline)
                    else:
                        require(time.monotonic() < deadline, "cleanup command deadline")
                    time.sleep(0.1)
                record["status"] = child.returncode
                require(time.monotonic() < deadline, "command completed after deadline")
            finally:
                if source is not None:
                    source.close()
        self.supervisor.refresh()
        require(out.stat().st_size <= 4 * 1024**2 and err.stat().st_size <= 4 * 1024**2, "command output quota")
        stdout, stderr = out.read_text(errors="replace"), err.read_text(errors="replace")
        if check:
            require(record["status"] == 0, f"command {arguments[0]} failed: {stderr[-4000:]}")
        return record["status"], stdout, stderr

    def execute(self, body, *, user=None, timeout=20, check=True):
        # Noninteractive stdin is finite; script bodies never use shell argv expansion.
        args = ["exec", self.name, "--quiet", "--no-tty", "--timeout", f"{timeout}s", "--workdir", "/"]
        if user is not None:
            suffix = "nobody" if user == "65534:65534" else "client"
            args.extend(["--user", user, "--env", f"HOME=/tmp/{self.nonce}-{suffix}"])
        args.extend(["--", self.spec["bash"] + "/bin/bash", "-se"])
        pid = self.inspect_running_vmm()
        # Pin the original process even if the supervisor reaps it meanwhile.
        fd = os.dup(self.supervisor.pidfds[pid])
        try:
            signal.pidfd_send_signal(fd, 0)
            result = self.command(args, timeout=timeout + 2, stdin="set -euo pipefail\n" + body, check=check)
            signal.pidfd_send_signal(fd, 0)
            require(self.inspect_running_vmm() == pid, "VMM replaced during exec")
            return result
        finally:
            os.close(fd)

    def inspect_running_vmm(self):
        record = decode(self.command(["inspect", self.name, "--format", "json"], timeout=5)[1])
        require(record.get("name") == self.name and record.get("status") == "Running",
                "refusing exec against a sandbox that is not Running")
        pids = self.runtime_pids()
        require(len(pids) == 1, "expected exactly one owned VMM before/after exec")
        return pids[0]

    def guest_json(self, command, *, user=None):
        return decode(self.execute(command + "\n", user=user)[1])

    def runtime_pids(self):
        self.supervisor.refresh()
        result = []
        for pid in self.supervisor.pidfds:
            try:
                if Path(f"/proc/{pid}/exe").resolve(strict=True) == self.msb:
                    result.append(pid)
            except (FileNotFoundError, ProcessLookupError):
                pass
        return result

    def capture_logs(self, cleanup_deadline=None):
        return self.command(["logs", self.name, "--source", "all", "--json"], check=True,
                            timeout=5, cleanup_deadline=cleanup_deadline)[1]

    def boot_diagnostics(self):
        """Retain failed-unit evidence; selected readiness is not all-unit health."""
        systemd = self.spec["systemd"]
        commands = {
            "failed_units": systemd + "/bin/systemctl --failed --plain --no-legend --no-pager",
            "sysctl_journal": systemd + "/bin/journalctl --boot=0 --unit=systemd-sysctl.service --lines=100 --no-pager --output=short-precise",
            "sysctl_unit": systemd + "/bin/systemctl show systemd-sysctl.service --property=Result,ExecMainCode,ExecMainStatus,ExecStart,StandardOutput,StandardError",
            "sysctl_config": systemd + "/lib/systemd/systemd-sysctl --cat-config --no-pager",
            "sysctl_journal_any_boot": systemd + "/bin/journalctl --unit=systemd-sysctl.service --lines=100 --no-pager --output=short-precise",
            "journal_boots": systemd + "/bin/journalctl --list-boots --no-pager",
            "sysctl_identifier": systemd + "/bin/journalctl --identifier=systemd-sysctl --lines=100 --no-pager --output=short-precise",
            "journal_warnings": systemd + "/bin/journalctl --priority=warning --lines=100 --no-pager --output=short-precise",
            "kernel_pid_max": self.spec["coreutils"] + "/bin/cat /proc/sys/kernel/pid_max",
        }
        snapshot = {}
        for name, command in commands.items():
            status, stdout, stderr = self.execute(command + "\n", timeout=5, check=False)
            require(len(stdout.encode()) + len(stderr.encode()) <= 128 * 1024, "boot diagnostic output budget")
            snapshot[name] = {"exit_status": status, "stdout": stdout, "stderr": stderr}
        units = snapshot["failed_units"]
        snapshot["unit_health"] = ("unavailable" if units["exit_status"] != 0 else
                                   "failed_units_observed" if units["stdout"].strip() else
                                   "no_failed_units_reported")
        self.report.setdefault("boot_diagnostics", []).append(snapshot)

    def setup_qa_user(self):
        """Add only the disposable test account, never change daemon policy."""
        home = f"/tmp/{self.nonce}-client"
        getent = "/run/current-system/sw/bin/getent"
        group = self.execute(getent + " group users\n")[1]
        require(group.strip().split(":") == ["users", "x", str(QA_GID), ""], "unexpected ordinary group")
        named = self.execute(getent + f" passwd {QA_NAME}\n", check=False)
        numeric = self.execute(getent + f" passwd {QA_UID}\n", check=False)
        previous = self.report.get("qa_user")
        if previous is None:
            # NSS errors are not absence, and collisions cannot authorize edits.
            require(named == numeric == (2, "", ""), "QA username/UID already exists or lookup failed")
            self.execute(f"test ! -e {home} && test ! -L {home}\n"
                         f"/run/current-system/sw/bin/useradd --uid {QA_UID} --gid users --no-create-home "
                         f"--no-user-group --home-dir {home} --shell /run/current-system/sw/bin/nologin {QA_NAME}\n")
        else:
            require(previous == {"name": QA_NAME, "uid": QA_UID, "gid": QA_GID, "home": home}, "unexpected retained QA identity")
            require(named[0] == numeric[0] == 0 and not named[2] and not numeric[2], "QA identity missing after restart")
        account = self.execute(getent + f" passwd {QA_NAME}\n")[1]
        by_uid = self.execute(getent + f" passwd {QA_UID}\n")[1]
        core = self.spec["coreutils"] + "/bin/"
        ids = self.execute(f"{core}id -u\n{core}id -g\n{core}id -G\n", user=f"{QA_UID}:{QA_GID}")[1]
        identity = qa_identity(account, by_uid, group, ids, home)
        # On a fresh boot /tmp is new; accept no existing path, not a symlink.
        self.execute(f"test ! -e {home} && test ! -L {home}\n"
                     f"{core}mkdir --mode=700 {home}\n{core}chown {QA_UID}:{QA_GID} {home}\n")
        self.report["qa_user"] = identity

    def daemon_failure_diagnostics(self):
        """Bounded read-only evidence; never replace the primary client failure."""
        systemd = self.spec["systemd"]
        commands = {
            "unit": systemd + "/bin/systemctl show nix-daemon.service --property=ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,ExecStart",
            "journal": systemd + "/bin/journalctl --unit=nix-daemon.service --lines=100 --no-pager --output=short-precise",
            "config": self.spec["engine"] + "/bin/nix config show --json",
        }
        snapshot = {}
        self.report.setdefault("daemon_failure_diagnostics", []).append(snapshot)
        for name, command in commands.items():
            try:
                status, stdout, stderr = self.execute(command + "\n", timeout=5, check=False)
                require(len(stdout.encode()) + len(stderr.encode()) <= 128 * 1024,
                        "daemon diagnostic output budget")
                snapshot[name] = {"exit_status": status, "stdout": stdout, "stderr": stderr}
            except BaseException as error:
                snapshot[name] = {"diagnostic_error": repr(error)}

    def check_daemon_trust(self):
        for user, field in ((None, "root_trusted"), (f"{QA_UID}:{QA_GID}", "qa_trusted")):
            info = self.guest_json(self.spec["engine"] + "/bin/nix store info --store daemon --json", user=user)
            require(type(info.get("trusted")) is bool, "missing actual daemon trust value")
            self.report[field] = info["trusted"]
        require(self.report["root_trusted"] and not self.report["qa_trusted"], "daemon trust boundary")
        service = self.execute(self.spec["systemd"] + "/bin/systemctl show nix-daemon.service --property ExecStart --value\n")[1]
        require(self.spec["nixd"] + "/bin/determinate-nixd" in service and self.spec["engine"] + "/bin" in service,
                "wrong daemon pair")

    def check_nobody_denial(self):
        attempts = []
        self.report.setdefault("nobody_attempts", []).append(attempts)
        self.report["nobody_denied"] = False
        command = self.spec["engine"] + "/bin/nix store info --store daemon --json\n"
        try:
            for _ in range(3):
                attempt = {}
                attempts.append(attempt)
                try:
                    status, stdout, stderr = self.execute(command, user="65534:65534", timeout=5, check=False)
                    require(len(stdout.encode()) + len(stderr.encode()) <= 128 * 1024,
                            "denied-client output budget")
                    attempt.update(exit_status=status, stdout=stdout, stderr=stderr)
                except BaseException as error:
                    attempt["execution_error"] = repr(error)
                    raise
                if retryable_denial_transport(status, stdout, stderr):
                    attempt["classification"] = "retryable_transport_not_denial"
                    continue
                denied_client(status, stdout, stderr)
                attempt["classification"] = "explicit_authorization_denial"
                self.report["nobody_denied"] = True
                return
            raise ValueError("explicit authorization denial not observed after three transport failures")
        except BaseException:
            try:
                self.daemon_failure_diagnostics()
            except BaseException as diagnostic_error:
                self.report.setdefault("daemon_failure_diagnostics", []).append(
                    {"diagnostic_error": repr(diagnostic_error)})
            raise

    def failed_full_sysctl_replay(self, original_pid):
        """Reapply guest settings for diagnosis only; never upgrade a verdict."""
        record = self.report["sysctl_replay"] = {"status": "not_completed", "reapplies_guest_settings": True}
        try:
            require(self.inspect_running_vmm() == original_pid, "original full-test VMM is not Running")
            command = ("SYSTEMD_LOG_LEVEL=debug SYSTEMD_LOG_TARGET=console " +
                       self.spec["systemd"] + "/lib/systemd/systemd-sysctl\n")
            status, stdout, stderr = self.execute(command, timeout=5, check=False)
            require(len(stdout.encode()) + len(stderr.encode()) <= 128 * 1024,
                    "sysctl replay output budget")
            require(self.inspect_running_vmm() == original_pid, "VMM changed during sysctl replay")
            record.update(status="recorded", exit_status=status, stdout=stdout, stderr=stderr)
            self.capture_logs()
        except BaseException as error:
            # The caller always re-raises the primary full-run failure.
            record["diagnostic_error"] = repr(error)

    def activation(self):
        spec = self.spec
        c, u, systemctl = spec["coreutils"] + "/bin/", spec["utilLinux"] + "/bin/", spec["systemd"] + "/bin/systemctl"
        body = f"""
test "$({c}readlink -f /proc/1/exe)" = {shlex.quote(spec['systemd'] + '/lib/systemd/systemd')}
test "$({c}readlink -f /run/current-system)" = {shlex.quote(spec['toplevel'])}
test "$({u}findmnt --noheadings --output FSTYPE --mountpoint /run)" = tmpfs
{systemctl} is-active --quiet guest-store-registration.service nix-daemon.socket determinate-nixd.socket
test -f /etc/hosts && test -f /etc/hostname && test -f /etc/resolv.conf
test -f /etc/ssl/certs/ca-certificates.crt && test ! -L /etc/ssl/certs/ca-certificates.crt
test -f /nix/var/nix/db/db.sqlite
test -f /run/microsandbox/agentd.log
printf 'ACTIVATED:{self.nonce}\\n'
"""
        deadline = min(self.deadline, time.monotonic() + 90)
        while True:
            status, output, _ = self.execute(body, timeout=5, check=False)
            if status == 0 and output == f"ACTIVATED:{self.nonce}\n":
                break
            require(time.monotonic() < deadline, "systemd activation not observed")
            time.sleep(0.25)
        self.boot_diagnostics()
        record = decode(self.command(["inspect", self.name, "--format", "json"])[1])
        validate_inspect(record, self.name, self.image)
        self.report["inspect"] = record
        self.report["activation"] = True
        require(len(self.runtime_pids()) == 1, "expected one owned VMM process")
        roots = " ".join(shlex.quote(path) for path in spec["roots"])
        actual = self.execute(f"{spec['engine']}/bin/nix-store --query --requisites {roots}\n")[1].splitlines()
        expected = (Path(spec["registration"]) / "store-paths").read_text().splitlines()
        require(set(actual) == set(expected) and len(actual) == len(set(actual)), "registered closure differs")
        for index, root in enumerate(spec["roots"]):
            self.execute(f"test \"$({c}readlink /nix/var/nix/gcroots/guest-images/base/{index})\" = {shlex.quote(root)}\n")
        ca = self.execute(c + "sha256sum /etc/ssl/certs/ca-certificates.crt\n")[1].split()[0]
        expected_ca = digest(Path(spec["roots"][0]) / "etc/ssl/certs/ca-certificates.crt")
        require(ca == expected_ca, "runtime CA bytes differ without injection")
        self.report["registration"] = True
        self.report["registered_paths"] = len(expected)
        self.report["runtime_ca_sha256"] = ca
        self.setup_qa_user()
        # Preserve successful positive controls independently of the negative.
        self.check_daemon_trust()
        nobody_home = f"/tmp/{self.nonce}-nobody"
        self.execute(f"test ! -e {nobody_home} && test ! -L {nobody_home}\n"
                     f"{c}mkdir --mode=700 {nobody_home}\n{c}chown 65534:65534 {nobody_home}\n")
        nobody_ids = self.execute(f"{c}id -u\n{c}id -g\n{c}id -G\n", user="65534:65534")[1]
        require(nobody_ids.splitlines() == ["65534", "65534", "65534"], "wrong disallowed process identity")
        self.check_nobody_denial()

    def run(self):
        require(self.command(["--version"])[1].strip() == "msb 0.6.16", "runtime version")
        require(decode(self.command(["list", "--format", "json"])[1]) == [], "fresh MSB store is not empty")
        tar = self.root / "image.tar"
        self.report["import_tar"] = unpack_archive(Path(self.spec["archive"]), tar, self.limits)
        self.command(["image", "load", "--input", str(tar), "--tag", self.image], timeout=120)
        self.attempted = True
        self.command(["create", self.image, "--name", self.name, "--pull", "never", "--init", "/init",
                      "--init-env", "container=microsandbox", "--workdir", "/", "--security", "default",
                      "--no-net", "--cpus", "1", "--memory", "2G", "--root-disk", "4G",
                      "--max-duration", "5m", "--log-level", "debug"], timeout=120)
        self.activation()
        if self.args.mode == "full":
            from smoke_full import full_acceptance
            original_pid = self.inspect_running_vmm()
            try:
                full_acceptance(self)
            except BaseException:
                self.failed_full_sysctl_replay(original_pid)
                raise
        elif self.args.mode == "shutdown-diagnostic":
            from shutdown_diagnostic import diagnose_shutdown
            diagnose_shutdown(self)

    def cleanup(self):
        deadline = time.monotonic() + 90
        errors, forced = [], False
        evidence = self.report["cleanup_diagnostics"] = {"snapshots": []}

        def snapshot(stage):
            try:
                evidence["snapshots"].append({"stage": stage, "owned": self.supervisor.snapshot(),
                                              "observed": time.monotonic()})
            except Exception as error:
                errors.append(f"{stage} snapshot: {error}")

        snapshot("before_cleanup")
        if self.attempted:
            try:
                # Preserve a bounded diagnostic snapshot before stopping a VMM.
                self.capture_logs(cleanup_deadline=deadline)
            except Exception as error:
                errors.append(str(error))
        if self.report.get("error"):
            try:
                before = len(self.supervisor.signal_events)
                self.supervisor.quiesce(timeout=10, grace=1)
                forced = len(self.supervisor.signal_events) != before
            except Exception as error:
                errors.append(str(error))
                forced = True
        if self.attempted:
            try:
                status, _, _ = self.command(["stop", self.name, "--timeout", "10"], timeout=20,
                                            check=False, cleanup_deadline=deadline)
                require(status == 0, "cleanup stop failed")
            except Exception as error:
                errors.append(str(error))
            snapshot("after_stop_before_quiescence")
            try:
                # Retain shutdown output before removal, independently of the
                # pre-stop snapshot. A successful CLI stop is not a flush oracle.
                self.capture_logs(cleanup_deadline=deadline)
            except Exception as error:
                errors.append(f"post-stop logs: {error}")
        try:
            before = len(self.supervisor.signal_events)
            self.supervisor.quiesce(timeout=10, grace=1)
            forced = forced or len(self.supervisor.signal_events) != before
        except Exception as error:
            errors.append(str(error))
            forced = True
        if self.attempted:
            try:
                self.command(["remove", self.name], timeout=10, cleanup_deadline=deadline)
            except Exception as error:
                errors.append(str(error))
        empty = False
        try:
            empty = decode(self.command(["list", "--format", "json"], timeout=10, cleanup_deadline=deadline)[1]) == []
        except Exception as error:
            errors.append(str(error))
        try:
            before = len(self.supervisor.signal_events)
            self.supervisor.close()
        except Exception as error:
            errors.append(str(error))
        finally:
            forced = forced or len(self.supervisor.signal_events) != before
        evidence["signal_events"] = list(self.supervisor.signal_events)
        evidence["exit_events"] = list(self.supervisor.exit_events)
        self.report["cleanup"] = {"empty_store": empty, "children_empty": not self.supervisor.children(),
                                  "forced": forced, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True, help="explicitly authorize this disposable VM test")
    parser.add_argument("--mode", choices=["activation", "full", "shutdown-diagnostic"], default="activation")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--msb", type=Path, required=True)
    parser.add_argument("--runtime-revision", required=True)
    parser.add_argument("--msb-sha256", required=True)
    parser.add_argument("--agentd-sha256", required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--scratch", type=Path, default=Path("/tmp"))
    args = parser.parse_args()
    fixture = Fixture(args)
    def interrupted(signum, _frame):
        raise InterruptedError(f"fixture interrupted by signal {signum}")
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    try:
        fixture.run()
    except BaseException as error:
        fixture.report["error"] = repr(error)
    finally:
        # A second ordinary interrupt must not abandon the bounded owned cleanup.
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, signal.SIG_IGN)
        fixture.cleanup()
        try:
            fixture.report["status"] = verdict(fixture.report)
            fixture.report["full_acceptance"] = fixture.report["status"] == "full_acceptance_passed"
        except Exception as error:
            fixture.report["status"] = "failed"
            fixture.report.setdefault("error", repr(error))
        fixture.report["elapsed_seconds"] = round(time.monotonic() - fixture.started, 3)
        (fixture.root / "result.json").write_text(json.dumps(fixture.report, indent=2) + "\n")
        print(json.dumps({"status": fixture.report["status"], "root": str(fixture.root)}))
    return int(fixture.report["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
