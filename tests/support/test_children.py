"""Standalone supervisor isolation tests; see NOTICE.md for source attribution."""

from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import children


class ChildSupervisorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="children-fixture-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_supervisor_rejects_pre_existing_children(self):
        with patch.object(children.ChildSupervisor, "children", return_value={123}):
            with self.assertRaisesRegex(RuntimeError, "pre-existing children"):
                children.ChildSupervisor()

    def test_supervisor_refuses_pidfd_that_is_not_our_child(self):
        supervisor = children.ChildSupervisor.__new__(children.ChildSupervisor)
        supervisor.processes = []
        supervisor.pidfds = {}
        supervisor.identities = {}
        with patch.object(supervisor, "children", return_value={123}), patch("children.os.pidfd_open", return_value=77), patch("children.os.waitid", side_effect=ChildProcessError), patch("children.os.close") as close, patch("children.signal.pidfd_send_signal") as send:
            supervisor.refresh()
        self.assertEqual(supervisor.pidfds, {})
        close.assert_called_once_with(77)
        send.assert_not_called()

    def test_timeout_reaps_detached_double_fork_before_late_state_creation(self):
        self.check_detached_child_cleanup(timeout=True)

    def test_successful_launcher_exit_still_owns_detached_grandchild(self):
        self.check_detached_child_cleanup(timeout=False)

    def test_refresh_records_only_observed_owned_exit(self):
        supervisor = children.ChildSupervisor.__new__(children.ChildSupervisor)
        supervisor.processes, supervisor.pidfds, supervisor.exit_events = [], {123: 77}, []
        supervisor.identities = {123: {"pid": 123, "executable": "/synthetic/runtime"}}
        status = SimpleNamespace(si_code=children.os.CLD_EXITED, si_status=0)
        with patch.object(supervisor, "children", return_value=set()), patch("children.os.waitid", return_value=status), patch("children.os.close") as close:
            supervisor.refresh()
        self.assertEqual(supervisor.pidfds, {})
        self.assertEqual({key: supervisor.exit_events[0][key] for key in ("pid", "code", "status")},
                         {"pid": 123, "code": children.os.CLD_EXITED, "status": 0})
        close.assert_called_once_with(77)
        self.assertEqual(supervisor.exit_events[0]["executable"], "/synthetic/runtime")

    def test_snapshot_reads_only_verified_owned_pidfds(self):
        supervisor = children.ChildSupervisor.__new__(children.ChildSupervisor)
        supervisor.pidfds = {123: 77}
        with patch.object(supervisor, "refresh"), patch("children.os.readlink", return_value="/synthetic/runtime") as read:
            self.assertEqual(supervisor.snapshot(), [{"pid": 123, "executable": "/synthetic/runtime"}])
        read.assert_called_once_with("/proc/123/exe")

    def test_signal_receipt_identifies_owned_child_without_arguments(self):
        supervisor = children.ChildSupervisor.__new__(children.ChildSupervisor)
        supervisor.pidfds, supervisor.signal_events = {123: 77}, []
        def refresh():
            if supervisor.signal_events:
                supervisor.pidfds = {}
        with patch.object(supervisor, "refresh", side_effect=refresh), patch.object(supervisor, "children", return_value=set()), patch.object(supervisor, "identity", return_value={"pid": 123, "executable": "/synthetic/helper"}), patch("children.signal.pidfd_send_signal") as send, patch("children.time.sleep"):
            supervisor.quiesce(timeout=1)
        send.assert_called_once_with(77, children.signal.SIGTERM)
        self.assertEqual(supervisor.signal_events[0]["pid"], 123)
        self.assertEqual(supervisor.signal_events[0]["executable"], "/synthetic/helper")
        self.assertIn("observed", supervisor.signal_events[0])

    def test_closed_supervisor_cannot_launch(self):
        supervisor = children.ChildSupervisor.__new__(children.ChildSupervisor)
        supervisor.closed = True
        with patch("children.subprocess.Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "closed"):
                supervisor.start(["never-execute"])
        popen.assert_not_called()

    def check_detached_child_cleanup(self, *, timeout):
        # The subreaper lives in a separate fixture process, not this test
        # process. Every fake descendant has a short self-expiry as a backstop.
        child = r'''
import json, os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        ready = Path(sys.argv[1])
        pending = ready.with_name(ready.name + ".pending")
        payload = json.dumps({"pid": os.getpid(), "group": os.getpgrp()})
        with pending.open("x") as output:
            # Exercise a partial write without publishing the readiness name.
            output.write(payload[:1])
            output.flush()
            time.sleep(0.05)
            output.write(payload[1:])
        pending.replace(ready)
        deadline = time.monotonic() + 5
        while not Path(sys.argv[3]).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if Path(sys.argv[3]).exists():
            Path(sys.argv[2]).write_text("late state must never be created")
        os._exit(0)
    os._exit(0)
if sys.argv[4] == "timeout":
    time.sleep(5)
os._exit(0)
'''
        controller = f'''
import json, os, select, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, {str(Path(children.__file__).parent)!r})
from children import ChildSupervisor
ready = Path({str(self.root / "ready")!r})
late = Path({str(self.root / "late")!r})
release = Path({str(self.root / "release")!r})
supervisor = ChildSupervisor()
try:
    timed_out = False
    try:
        result = supervisor.run([sys.executable, "-B", "-c", {child!r}, str(ready), str(late), str(release), {"timeout" if timeout else "exit"!r}], timeout=0.2)
        assert result == 0
    except subprocess.TimeoutExpired:
        timed_out = True
    assert timed_out == {timeout!r}
    deadline = time.monotonic() + 1
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    evidence = json.loads(ready.read_text())
    assert evidence["group"] != os.getpgrp()
    descendant_fd = os.pidfd_open(evidence["pid"])
    try:
        supervisor.quiesce(timeout=3, grace=0.05)
        exited = select.poll()
        exited.register(descendant_fd, select.POLLIN)
        assert exited.poll(1000), "detached descendant is still alive"
    finally:
        os.close(descendant_fd)
    assert supervisor.children() == set()
    assert supervisor.signal_events, "forced descendant cleanup was not recorded"
    release.write_text("allow the delayed state write only AFTER cleanup")
    time.sleep(0.05)
    assert not late.exists(), "detached producer escaped cleanup"
finally:
    supervisor.close()
print("OWNED_CHILDREN_REAPED")
'''
        result = subprocess.run([sys.executable, "-B", "-c", controller], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OWNED_CHILDREN_REAPED", result.stdout)


if __name__ == "__main__":
    unittest.main()
