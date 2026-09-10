"""Guest-requested poweroff diagnostics, never the normal host-stop acceptance."""

import os
import time

from smoke_contract import FALLBACK, require
from smoke_full import log_text, observe_vmm_exit, prepare_full_acceptance


OBSERVATION_SECONDS = 30
SNAPSHOT_BYTES = 128 * 1024


def process_snapshot(spec):
    # Deliberately omit environ and command arguments. Only argv[0] identifies
    # systemd's documented root-storage-daemon exclusion; no process is changed.
    c = spec["coreutils"] + "/bin/"
    return f"""set -eu
count=0
for proc in /proc/[0-9]*; do
  test -d "$proc" || continue
  count=$((count + 1))
  test "$count" -le 128
  printf 'PROCESS:%s\\n' "$proc"
  while IFS= read -r row; do
    case "$row" in
      Name:*|State:*|Tgid:*|Pid:*|PPid:*|Uid:*|Gid:*|SigPnd:*|ShdPnd:*|SigBlk:*|SigIgn:*|SigCgt:*) printf '%s\\n' "$row" ;;
    esac
  done < "$proc/status" || true
  printf 'EXE:'; {c}readlink "$proc/exe" || true
  printf 'WCHAN:'; {c}head -c 512 "$proc/wchan" || true; printf '\\n'
  printf 'CGROUP:'; {c}head -c 2048 "$proc/cgroup" || true; printf '\\n'
  first=''
  IFS= read -r -d '' -n 512 first < "$proc/cmdline" || true
  printf 'ARGV0:%s\\n' "${{first:0:512}}"
done
"""


def snapshot(fixture, record):
    systemctl = fixture.spec["systemd"] + "/bin/systemctl"
    commands = {
        "daemon_policy": systemctl + " show nix-daemon.service --property=ActiveState,SubState,MainPID,ControlPID,KillMode,KillSignal,FinalKillSignal,SendSIGKILL,TimeoutStopUSec,ExecStart,ExecStop,ControlGroup",
        "manager_policy": systemctl + " show --property=DefaultTimeoutStopUSec",
        "active_units": systemctl + " list-units --state=active,activating,deactivating --plain --no-legend --no-pager",
        "processes": process_snapshot(fixture.spec),
    }
    snapshots = record["pre_stop"] = {}
    for name, command in commands.items():
        status, stdout, stderr = fixture.execute(command + "\n", timeout=5, check=False)
        require(len(stdout.encode()) + len(stderr.encode()) <= SNAPSHOT_BYTES,
                "shutdown snapshot quota")
        snapshots[name] = {"exit_status": status, "stdout": stdout, "stderr": stderr}
        require(status == 0, "shutdown snapshot failed")


def validate_diagnostic(record):
    require(record.get("request") == "guest-systemctl-no-block", "wrong diagnostic request")
    require(record.get("request_status") == 0, "guest poweroff request not acknowledged")
    require(record.get("runtime_exit") == {"code": os.CLD_EXITED, "status": 0},
            "diagnostic runtime exit not normal")
    require(0 <= record.get("elapsed", -1) < OBSERVATION_SECONDS, "diagnostic poweroff deadline")
    require(record.get("flush_marker") is True and record.get("kernel_power_down") is True,
            "diagnostic poweroff sequence incomplete")
    require(record.get("logs_status") == 0 and not record.get("signals"),
            "diagnostic logs/owned process evidence incomplete")
    logs = record.get("logs", "")
    require("Power off not available: System halted instead" not in logs
            and not any(marker in logs for marker in FALLBACK), "diagnostic fallback observed")


def observe_guest_poweroff(fixture, marker, record):
    pid = fixture.inspect_running_vmm()
    fd = os.dup(fixture.supervisor.pidfds[pid])
    record.update(request="guest-systemctl-no-block", runtime_pid=pid, runtime_exit=None)
    previous_signals = len(fixture.supervisor.signal_events)
    started = time.monotonic()
    try:
        require(os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None,
                "original VMM already exited")
        # This is intentionally not Fixture.execute: its postcondition requires
        # a still-running VM, whereas this command asks that same VM to exit.
        # The read-only precheck is not atomic with CLI exec; any replacement
        # observed afterward fails. No host stop/grace override is involved.
        argv = ["exec", fixture.name, "--quiet", "--no-tty", "--timeout", "5s", "--workdir", "/",
                "--", fixture.spec["systemd"] + "/bin/systemctl", "--system", "--no-block",
                "--no-ask-password", "--job-mode=replace-irreversibly", "start", "poweroff.target"]
        try:
            status, stdout, stderr = fixture.command(argv, timeout=7, check=False)
            record.update(request_status=status, request_stdout=stdout, request_stderr=stderr)
        except InterruptedError:
            raise
        except Exception as error:
            record["request_error"] = repr(error)
        record["request_elapsed"] = time.monotonic() - started
        observation_error = None
        try:
            record["runtime_exit"] = observe_vmm_exit(
                fixture, pid, fd, started, min(fixture.deadline, started + OBSERVATION_SECONDS))
        except InterruptedError:
            raise
        except Exception as error:
            observation_error = error
            record["observation_error"] = repr(error)
        record["elapsed"] = time.monotonic() - started
        record["within_normal_host_grace"] = record["elapsed"] < 8
        log_error = None
        try:
            raw = fixture.capture_logs()
            record["logs_status"] = 0  # capture_logs requires a successful CLI read.
            record["logs"] = log_text(raw)
            console = log_text(raw, source="system")
            record["flush_marker"] = marker in console.splitlines()
            record["kernel_power_down"] = "Powering off." in console and "reboot: Power down" in console
        except InterruptedError:
            raise
        except Exception as error:
            log_error = error
            record["log_error"] = repr(error)
        record["signals"] = fixture.supervisor.signal_events[previous_signals:]
        identity_error = None
        try:
            record["remaining_runtime_pids"] = fixture.runtime_pids()
            require(not any(other != pid for other in record["remaining_runtime_pids"]),
                    "replacement VMM observed")
        except InterruptedError:
            raise
        except Exception as error:
            identity_error = error
            record["identity_error"] = repr(error)
        if observation_error is not None:
            raise observation_error
        if identity_error is not None:
            raise identity_error
        if log_error is not None:
            raise log_error
        validate_diagnostic(record)
        require(not fixture.runtime_pids(), "diagnostic VMM survived")
    finally:
        record["signals"] = fixture.supervisor.signal_events[previous_signals:]
        os.close(fd)


def diagnose_shutdown(fixture):
    _, _, marker = prepare_full_acceptance(fixture)
    record = fixture.report["shutdown_diagnostic"] = {"normal_host_stop_acceptance": "not_established"}
    snapshot(fixture, record)
    observe_guest_poweroff(fixture, marker, record)
