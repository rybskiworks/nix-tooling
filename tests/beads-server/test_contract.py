import tempfile
import subprocess
import signal
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from contract import DATABASE, classify_claim_rejection, client_env, decode_json, owned_path, validate_endpoint, verify_claim, verify_claim_commands, verify_identity, verify_rejection
from server import Fixture, stop_children


class ContractTests(unittest.TestCase):
    def test_only_exact_claim_serialization_can_precede_explicit_retry(self):
        message = ("Error claiming sqlcheck-one: dolt commit: Error 1213 (40001): serialization failure: "
                   "this transaction conflicts with a committed transaction from another client, try restarting transaction.")
        self.assertEqual(classify_claim_rejection((1, "", message), "sqlcheck-one", "alice", True), "serialization-rollback")
        with self.assertRaises(AssertionError):
            classify_claim_rejection((1, "", message), "sqlcheck-one", "alice")
        for error in ["access denied", "connection refused", "schema mismatch", message.replace("1213", "1205"),
                      message.replace("40001", "HY000"), message.replace("sqlcheck-one", "sqlcheck-other"),
                      message + "\nconnection lost", "issue already claimed by alice-other"]:
            with self.subTest(error=error), self.assertRaises(AssertionError):
                classify_claim_rejection((1, "", error), "sqlcheck-one", "alice", True)
        for result in [(0, "", message), (1, "[]", message)]:
            with self.assertRaises(AssertionError):
                classify_claim_rejection(result, "sqlcheck-one", "alice", True)
        self.assertEqual(classify_claim_rejection((1, "", "Error claiming sqlcheck-one: issue already claimed by alice\n"),
                                                  "sqlcheck-one", "alice"), "already-claimed")

    def test_environment_has_no_ambient_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict("os.environ", {"AWS_SECRET_ACCESS_KEY": "ambient", "BEADS_DOLT_SERVER_SOCKET": "/ambient"}):
                env = client_env(root, root / "alice", "/explicit/bin", "127.0.0.1", 3309, "alice", "synthetic", root / "ca.pem")
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
            self.assertNotIn("BEADS_DOLT_SERVER_SOCKET", env)
            self.assertEqual(env["BEADS_DOLT_AUTO_START"], "0")
            self.assertEqual(env["BEADS_DOLT_SERVER_TLS"], "1")
            self.assertEqual(env["BEADS_DOLT_SERVER_DATABASE"], DATABASE)

    def test_endpoint_rejects_external_host_and_invalid_ports(self):
        for host, port in [("example.com", 3309), ("0.0.0.0", 3309), ("127.0.0.1", True), ("127.0.0.1", 0), ("127.0.0.1", 65536)]:
            with self.subTest(host=host, port=port), self.assertRaises(AssertionError):
                validate_endpoint(host, port)

    def test_path_rejects_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(AssertionError):
                owned_path(root, root.parent / "other")
            with self.assertRaises(AssertionError):
                owned_path(root, root)

    def test_path_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alias").symlink_to(root)
            with self.assertRaises(AssertionError):
                owned_path(root, root / "alias" / "file")

    def test_identity_rejects_missing_or_different_id(self):
        good = {"project_id": "expected", "dolt_mode": "server", "dolt_database": DATABASE}
        self.assertEqual(verify_identity(good, "expected"), "expected")
        for metadata, remote in [(good, ""), (good, "other"), ({**good, "project_id": ""}, "expected"), ({**good, "dolt_mode": "embedded"}, "expected"), ({**good, "dolt_database": "other"}, "expected")]:
            with self.subTest(metadata=metadata, remote=remote), self.assertRaises(AssertionError):
                verify_identity(metadata, remote)

    def test_claim_requires_one_winner_and_exact_durable_assignee(self):
        issue = {"status": "in_progress", "assignee": "alice"}
        self.assertEqual(verify_claim([0, 1], issue, ["alice", "bob"]), "alice")
        for results, actors in [([0, 0], ["alice", "bob"]), ([1, 1], ["alice", "bob"]), ([0, 1], ["alice", "alice"]), ([1, 0], ["alice", "bob"])]:
            with self.subTest(results=results, actors=actors), self.assertRaises(AssertionError):
                verify_claim(results, issue, actors)

    def test_json_error_is_not_success(self):
        with self.assertRaises(AssertionError):
            decode_json('{"error":"failed"}')
        self.assertEqual(decode_json('[{"id":"sqlcheck-one"}]')[0]["id"], "sqlcheck-one")

    def test_unrelated_error_cannot_satisfy_negative_control(self):
        verify_rejection(1, "TLS certificate unknown authority", ["unknown authority"], "CA")
        for status, error in [(0, "unknown authority"), (1, "schema unavailable")]:
            with self.assertRaises(AssertionError):
                verify_rejection(status, error, ["unknown authority"], "CA")

    def test_losing_claim_must_be_the_expected_conflict(self):
        issue = {"id": "sqlcheck-one", "status": "in_progress", "assignee": "alice"}
        winner = (0, '[{"id":"sqlcheck-one","status":"in_progress","assignee":"alice"}]', "")
        self.assertEqual(verify_claim_commands([winner, (1, "", "issue already claimed by alice")], issue, ["alice", "bob"]), "alice")
        for error in ["schema mismatch", "access denied", "connection refused", "issue already claimed by someoneelse"]:
            with self.subTest(error=error), self.assertRaises(AssertionError):
                verify_claim_commands([winner, (1, "", error)], issue, ["alice", "bob"])
        with self.assertRaises(AssertionError):
            verify_claim_commands([(0, "[]", ""), (1, "", "issue already claimed by alice")], issue, ["alice", "bob"])


class FakeChild:
    def __init__(self, pid, events, term_timeout=False):
        self.pid = pid
        self.events = events
        self.returncode = None
        self.term_timeout = term_timeout

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.events.append(("wait", self.pid))
        if self.term_timeout:
            self.term_timeout = False
            raise subprocess.TimeoutExpired("synthetic", timeout)
        self.returncode = 0
        return 0


class SupervisorTests(unittest.TestCase):
    def test_sql_preserves_literal_percent_and_bound_account_values(self):
        fixture = Fixture.__new__(Fixture)
        fixture.check = Mock()
        fixture.root = Path("/synthetic-owned-root")
        fixture.port = 3309
        fixture.passwords = {"root": "synthetic-secret"}
        mysql = MagicMock()
        cursor = mysql.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        with patch.dict("sys.modules", {"pymysql": mysql}), patch("server.ssl.create_default_context"):
            fixture.sql("SELECT '100%'")
            cursor.execute.assert_called_with("SELECT '100%'", None)
            query = "CREATE USER %s@%s IDENTIFIED BY %s"
            parameters = ("alice", "%", "synthetic-secret")
            fixture.sql(query, parameters)
            cursor.execute.assert_called_with(query, parameters)

    def test_diagnostics_redact_every_synthetic_password(self):
        fixture = Fixture.__new__(Fixture)
        fixture.passwords = {"alice": "secret-alice", "bob": "secret-bob"}
        self.assertEqual(fixture.redact("SQL: secret-alice / secret-bob"),
                         "SQL: <synthetic-password> / <synthetic-password>")

    def test_normal_server_shutdown_is_awaited(self):
        events = []
        child = FakeChild(100, events)
        result = stop_children([child], child, lambda pid, sig: events.append((sig, pid)))
        self.assertEqual(events[:2], [(signal.SIGTERM, 100), ("wait", 100)])
        self.assertEqual(result["forced_pids"], [])
        self.assertTrue(result["all_registered_children_reaped"])

    def test_timeout_forces_exact_owned_child_and_reports_it(self):
        events = []
        child = FakeChild(100, events, term_timeout=True)
        result = stop_children([child], child, lambda pid, sig: events.append((sig, pid)))
        self.assertIn((signal.SIGKILL, 100), events)
        self.assertEqual(result["forced_pids"], [100])
        self.assertTrue(result["all_registered_children_reaped"])

    def test_termination_error_does_not_skip_other_child_reaping(self):
        events = []
        children = [FakeChild(100, events), FakeChild(101, events)]
        def send(pid, sig):
            if sig == signal.SIGTERM:
                raise PermissionError("synthetic termination failure")
            events.append((sig, pid))
        result = stop_children(children, children[0], send)
        self.assertTrue(result["errors"])
        self.assertTrue(result["all_registered_children_reaped"])
        self.assertIn(("wait", 101), events)

    def test_cleanup_ignores_exhausted_work_budget_and_preserves_failure(self):
        fixture = Fixture.__new__(Fixture)
        fixture.stop_monitor = threading.Event()
        fixture.meter = Mock()
        fixture.lock = threading.Lock()
        fixture.children = []
        fixture.server = None
        fixture.failure = "scratch quota exceeded"
        fixture.started = time.monotonic()
        fixture.report = {"error": "original error"}
        fixture.limits = Mock(side_effect=AssertionError("must not be called"))
        with self.assertRaisesRegex(AssertionError, "scratch quota exceeded"):
            fixture.close()
        fixture.limits.assert_not_called()
        self.assertTrue(fixture.report["cleanup"]["all_registered_children_reaped"])
        self.assertEqual(fixture.report["error"], "original error")

    def test_resource_guards_reject_disk_log_and_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture.__new__(Fixture)
            fixture.root = Path(directory)
            fixture.deadline = time.monotonic() + 60
            fixture.listener_allowed = True
            fixture.lock = threading.Lock()
            fixture.children = []
            fixture.log_files = []
            fixture.report = {}
            with patch("server.shutil.disk_usage", return_value=Mock(free=0)):
                with self.assertRaisesRegex(AssertionError, "disk free floor"):
                    fixture.limits()
            log = fixture.root / "oversized.log"
            with log.open("wb") as output:
                output.truncate(4 * 1024**2 + 1)
            fixture.log_files = [log]
            with patch("server.shutil.disk_usage", return_value=Mock(free=20 * 1024**3)):
                with self.assertRaisesRegex(AssertionError, "log exceeded"):
                    fixture.limits()
            fixture.deadline = 0
            with self.assertRaisesRegex(AssertionError, "deadline"):
                fixture.limits()

    def test_catalog_preserves_exact_schema_identity_and_history(self):
        fixture = Fixture.__new__(Fixture)
        replies = {
            "SHOW TABLES": [{"Tables_in_beads_contract": "metadata"}],
            "SHOW CREATE TABLE `metadata`": [{"Create Table": "CREATE TABLE metadata (value TEXT)"}],
            "SELECT value AS project_id FROM metadata WHERE `key`='_project_id'": [{"project_id": "expected"}],
            "SELECT commit_hash FROM dolt_log": [{"commit_hash": "native-head"}],
            "SELECT name, hash FROM dolt_branches ORDER BY name": ({"name": "main", "hash": "native-head"},),
        }
        catalog = fixture.catalog(replies.__getitem__)
        self.assertEqual(catalog["project_id"], "expected")
        self.assertEqual(catalog["schema"], {"metadata": {"kind": "table", "definition": "CREATE TABLE metadata (value TEXT)"}})
        self.assertEqual(catalog["history"], ["native-head"])
        self.assertEqual(catalog["branches"], [{"name": "main", "hash": "native-head"}])
        replies["SHOW TABLES"].append({"Tables_in_beads_contract": "ready_issues"})
        replies["SHOW CREATE TABLE `ready_issues`"] = [{"Create View": "CREATE VIEW ready_issues AS SELECT 1", "View": "ready_issues"}]
        self.assertEqual(fixture.catalog(replies.__getitem__)["schema"]["ready_issues"],
                         {"kind": "view", "definition": "CREATE VIEW ready_issues AS SELECT 1"})
        replies["SHOW CREATE TABLE `ready_issues`"] = [{"View": "ready_issues"}]
        with self.assertRaisesRegex(AssertionError, "missing or ambiguous"):
            fixture.catalog(replies.__getitem__)
        replies["SHOW TABLES"] = [{"table": "unsafe`name"}]
        with self.assertRaisesRegex(AssertionError, "unsafe table"):
            fixture.catalog(replies.__getitem__)

    def test_backup_inventory_detects_content_changes_and_symlinks(self):
        fixture = Fixture.__new__(Fixture)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "chunk").write_bytes(b"native-bytes")
            before = fixture.backup_inventory(root)
            self.assertEqual(fixture.backup_inventory(root), before)
            (root / "chunk").write_bytes(b"changed-bytes")
            self.assertNotEqual(fixture.backup_inventory(root), before)
            (root / "alias").symlink_to(root / "chunk")
            with self.assertRaisesRegex(AssertionError, "backup symlink"):
                fixture.backup_inventory(root)

    def test_offline_bootstrap_rejects_a_tcp_listener(self):
        fixture = Fixture.__new__(Fixture)
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "read_text", return_value="header\n0: 0100007F:1234 00000000:0000 0A\n"):
            with self.assertRaisesRegex(AssertionError, "unexpected TCP listener"):
                fixture.no_tcp_listeners()


if __name__ == "__main__":
    unittest.main()
