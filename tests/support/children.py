"""Linux pidfd/subreaper ownership for standalone, single-threaded test runners.

Adapted from rybskiworks/workestrate-fleet-georgrybski; see NOTICE.md.
"""

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class ChildSupervisor:
    """Own and reap this single-threaded runner's children, including double forks."""

    PR_SET_CHILD_SUBREAPER = 36
    PR_GET_CHILD_SUBREAPER = 37

    def __init__(self):
        if sys.platform != "linux" or not all((
            hasattr(os, "pidfd_open"), hasattr(os, "P_PIDFD"),
            hasattr(signal, "pidfd_send_signal"),
        )):
            raise RuntimeError("child supervision requires Linux pidfds and child subreaping")
        if self.children():
            raise RuntimeError("child supervision requires a runner without pre-existing children")
        # Check kernel support before launching anything that would need cleanup.
        fd = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(fd, 0)
            try:
                os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                pass  # We are not our own child; the pidfd operation is supported.
        finally:
            os.close(fd)
        self.libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        if self.libc.prctl(self.PR_GET_CHILD_SUBREAPER, ctypes.byref(previous), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot read child-subreaper setting")
        if self.libc.prctl(self.PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot enable child subreaping")
        self.previous = previous.value
        self.processes = []
        self.pidfds = {}
        self.closed = False
        self.exit_events = []
        self.signal_events = []
        self.identities = {}

    @staticmethod
    def children():
        # Read only our own child lists, never scan process names or environments.
        children = set()
        for task in Path("/proc/self/task").iterdir():
            try:
                children.update(map(int, (task / "children").read_text().split()))
            except FileNotFoundError:
                pass
        return children

    def run(self, command, *, timeout, **kwargs):
        deadline = time.monotonic() + timeout
        process = self.start(command, **kwargs)
        # Unlike subprocess.run, a timeout leaves the whole owned launch for
        # coordinated quiescence, not just a killed immediate launcher.
        return process.wait(timeout=max(0, deadline - time.monotonic()))

    def start(self, command, **kwargs):
        """Register a launch while callers monitor their own deadline/quotas."""
        if self.closed:
            raise RuntimeError("cannot launch through closed child supervision")
        process = subprocess.Popen(command, **kwargs)
        self.processes.append(process)
        return process

    @staticmethod
    def identity(pid):
        # Called only after waitid proves this pidfd belongs to our child.
        # Read no command arguments, environments or unrelated process state.
        try:
            executable = os.readlink(f"/proc/{pid}/exe")
        except FileNotFoundError:
            executable = None
        except PermissionError:
            executable = "unreadable"
        return {"pid": pid, "executable": executable}

    def snapshot(self):
        self.refresh()
        return [self.identity(pid) for pid in sorted(self.pidfds)]

    def refresh(self):
        self.processes = [process for process in self.processes if process.poll() is None]
        for pid in self.children():
            if pid in self.pidfds:
                continue
            try:
                fd = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            try:
                # A pidfd pins identity; waitid also proves it is OUR child.
                # If a listed PID exited and was reused, ECHILD refuses it.
                os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                os.close(fd)
                continue
            except BaseException:
                os.close(fd)
                raise
            self.pidfds[pid] = fd
            self.identities[pid] = self.identity(pid)
        for pid, fd in list(self.pidfds.items()):
            try:
                status = os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if status is None:
                    continue
                os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG)
                self.exit_events.append({**self.identities.get(pid, {"pid": pid}), "code": status.si_code,
                                         "status": status.si_status, "observed": time.monotonic()})
            except ChildProcessError:
                pass  # Popen.poll already reaped this direct launcher.
            os.close(fd)
            del self.pidfds[pid]

    def quiesce(self, timeout=5, grace=1):
        """Stop exact owned producers before checking the final backend state."""
        deadline = time.monotonic() + timeout
        gentle_until = time.monotonic() + grace
        signalled = set()
        while True:
            self.refresh()
            if not self.pidfds and not self.children():
                return
            now = time.monotonic()
            if now >= deadline:
                raise RuntimeError(f"owned children did not exit before cleanup deadline: {sorted(self.pidfds)}")
            sig = signal.SIGTERM if now < gentle_until else signal.SIGKILL
            for pid, fd in self.pidfds.items():
                key = (fd, sig)
                if sig == signal.SIGKILL or key not in signalled:
                    try:
                        identity = self.identity(pid)
                        signal.pidfd_send_signal(fd, sig)
                        self.signal_events.append({**identity, "pidfd": fd, "signal": int(sig),
                                                   "observed": time.monotonic()})
                    except ProcessLookupError:
                        pass
                    signalled.add(key)
            # Parent exit reparents even new-process-group descendants to us.
            # Repeatedly adopt/reap until there can be no late creator left.
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    def close(self):
        if not self.closed:
            self.quiesce()
            if self.libc.prctl(self.PR_SET_CHILD_SUBREAPER, self.previous, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "cannot restore child-subreaper setting")
            self.closed = True
