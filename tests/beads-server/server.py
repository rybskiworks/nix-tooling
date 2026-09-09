"""Opt-in native Beads/MySQL compatibility test. Never uses an existing tracker."""

import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import tempfile
import threading
import time

from contract import DATABASE, DATABASE_PREFIX, classify_claim_rejection, client_env, decode_json, require, verify_claim_commands, verify_identity, verify_rejection


def stop_children(children, server, send_signal=os.killpg):
    """Reap every registered child, retaining all errors within one cleanup budget."""
    deadline = time.monotonic() + 25
    forced, errors = [], []
    if server is not None and server.poll() is None:
        try:
            send_signal(server.pid, signal.SIGTERM)
            server.wait(timeout=10)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            pass
        except Exception as error:
            errors.append(f"server termination: {error}")
    for child in children:
        if child.poll() is None:
            forced.append(child.pid)
            try:
                send_signal(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception as error:
                errors.append(f"kill {child.pid}: {error}")
        try:
            child.wait(timeout=max(0.01, min(5, deadline - time.monotonic())))
        except Exception as error:
            errors.append(f"reap {child.pid}: {error}")
    return {"forced_pids": forced, "errors": errors,
            "all_registered_children_reaped": all(p.returncode is not None for p in children),
            "server_exit_code": None if server is None else server.returncode}


class Fixture:
    def __init__(self, args):
        self.args = args
        self.root = Path(tempfile.mkdtemp(prefix="beads-server-", dir=args.scratch)).resolve()
        self.root.chmod(0o700)
        self.started = time.monotonic()
        self.deadline = self.started + 540
        self.children = []
        self.lock = threading.Lock()
        self.failure = None
        self.stop_monitor = threading.Event()
        self.passwords = {user: secrets.token_hex(24) for user in ["root", "reader", "alice", "bob"]}
        self.report = {"status": "running", "stages": [], "children": [], "root": str(self.root)}
        self.paths = os.pathsep.join(str(Path(p).parent) for p in [args.bd, args.dolt, args.git, args.openssl])
        self.port = 0
        self.server = None
        self.listener_allowed = False
        self.sequence = 0
        self.log_files = []
        self.meter = threading.Thread(target=self.monitor, daemon=True)
        self.meter.start()

    def redact(self, value):
        value = str(value)
        for password in self.passwords.values():
            value = value.replace(password, "<synthetic-password>")
        return value

    def limits(self):
        require(time.monotonic() < self.deadline, "fixture deadline exceeded")
        if not self.listener_allowed:
            self.no_tcp_listeners()
        require(shutil.disk_usage(self.root).free >= 12 * 1024**3, "disk free floor reached")
        allocated = 0
        for path in self.root.rglob("*"):
            # Dolt atomically replaces temporary files while the sample runs.
            with contextlib.suppress(FileNotFoundError):
                allocated += path.lstat().st_blocks * 512
        require(allocated <= 500 * 1024**2, "fixture allocated scratch exceeded 500 MiB")
        rss = 0
        with self.lock:
            children = list(self.children)
        for child in children:
            if child.poll() is not None:
                continue
            try:
                fields = Path(f"/proc/{child.pid}/statm").read_text().split()
                rss += int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
            except FileNotFoundError:
                pass
        require(rss <= 2 * 1024**3, "owned child RSS exceeded 2 GiB")
        require(all(path.stat().st_size <= 4 * 1024**2 for path in self.log_files), "child log exceeded 4 MiB")
        self.report["peak_scratch_bytes"] = max(allocated, self.report.get("peak_scratch_bytes", 0))
        self.report["peak_child_rss_bytes"] = max(rss, self.report.get("peak_child_rss_bytes", 0))

    def monitor(self):
        while not self.stop_monitor.wait(0.5):
            try:
                self.limits()
            except Exception as error:
                self.failure = self.redact(error)
                with self.lock:
                    children = list(self.children)
                for child in children:
                    if child.poll() is None:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(child.pid, signal.SIGKILL)
                return

    def check(self):
        require(self.failure is None, self.failure or "resource monitor failed")
        self.limits()

    def launch(self, command, env, cwd, name):
        self.check()
        with self.lock:
            self.sequence += 1
            serial = self.sequence
        stdout = self.root / f"{serial:03d}-{name}.stdout"
        stderr = self.root / f"{serial:03d}-{name}.stderr"
        with stdout.open("xb") as out, stderr.open("xb") as err:
            self.log_files.extend([stdout, stderr])
            child = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
        with self.lock:
            self.children.append(child)
        return child, stdout, stderr

    def run(self, command, env, cwd, name, expect=0):
        child, out, err = self.launch(command, env, cwd, name)
        try:
            status = child.wait(timeout=min(60, max(0.1, self.deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
            raise AssertionError(f"{name} timed out")
        self.check()
        stdout, stderr = self.redact(out.read_text()), self.redact(err.read_text())
        self.report["children"].append({"pid": child.pid, "name": name, "exit_code": status})
        if expect is not None:
            require(status == expect, f"{name}: exit {status}: {stderr[-6000:]}")
        return status, stdout, stderr

    def env(self, user, client=None):
        client = self.root / user if client is None else client
        env = client_env(self.root, client, self.paths, "127.0.0.1", self.port, user, self.passwords[user], self.root / "ca.pem")
        for name in ["HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "DOLT_ROOT_PATH", "SSL_CERT_DIR"]:
            Path(env[name]).mkdir(parents=True, exist_ok=True, mode=0o700)
        (client / "repo").mkdir(parents=True, exist_ok=True, mode=0o700)
        return env

    def sql(self, query, params=None, user="root", database=DATABASE, password=None, records=False):
        import pymysql
        self.check()
        tls = ssl.create_default_context(cafile=str(self.root / "ca.pem"))
        with pymysql.connect(host="127.0.0.1", port=self.port, user=user, password=self.passwords[user] if password is None else password,
                             database=database, ssl=tls, connect_timeout=3, read_timeout=10, write_timeout=10, autocommit=True,
                             cursorclass=pymysql.cursors.DictCursor if records else pymysql.cursors.Cursor) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchall()

    def bd(self, user, arguments, expect=0, env_extra=None, name="bd"):
        env = self.env(user)
        if env_extra:
            env.update(env_extra)
        return self.run([self.args.bd, "--sandbox", *arguments], env, self.root / user / "repo", name, expect)

    def stage(self, name, value=True):
        self.check()
        self.report["stages"].append({"name": name, "result": value})

    def no_tcp_listeners(self):
        for name in ["tcp", "tcp6"]:
            path = Path("/proc/net") / name
            if path.exists():
                rows = path.read_text().splitlines()[1:]
                require(not any(row.split()[3] == "0A" for row in rows), "unexpected TCP listener during offline bootstrap")

    def local_sql(self, directory, query):
        self.no_tcp_listeners()
        _, output, _ = self.run([self.args.dolt, "sql", "--disable-auto-gc", "--result-format", "json", "--query", query],
                                self.env("root"), directory, "offline-sql")
        rows = decode_json(output)["rows"]
        require(isinstance(rows, list), "invalid local SQL result")
        return rows

    def catalog(self, query):
        tables = []
        for row in query("SHOW TABLES"):
            require(len(row) == 1, "unexpected table inventory result")
            table = next(iter(row.values()))
            require(isinstance(table, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table), "unsafe table identifier")
            tables.append(table)
        require(tables and len(set(tables)) == len(tables), "empty or duplicate table inventory")
        schema = {}
        for table in sorted(tables):
            rows = query(f"SHOW CREATE TABLE `{table}`")
            require(len(rows) == 1, "invalid schema definition result")
            definitions = [key for key in ["Create Table", "Create View"] if key in rows[0]]
            require(len(definitions) == 1, "missing or ambiguous table/view definition")
            key = definitions[0]
            require(isinstance(rows[0][key], str) and rows[0][key], "invalid schema definition text")
            schema[table] = {"kind": "table" if key == "Create Table" else "view", "definition": rows[0][key]}
        project = query("SELECT value AS project_id FROM metadata WHERE `key`='_project_id'")
        require(len(project) == 1 and project[0]["project_id"], "missing SQL project identity")
        history = sorted(row["commit_hash"] for row in query("SELECT commit_hash FROM dolt_log"))
        require(history, "missing native history")
        branches = [dict(row) for row in query("SELECT name, hash FROM dolt_branches ORDER BY name")]
        return {"project_id": project[0]["project_id"], "schema": schema, "history": history, "branches": branches}

    def backup_inventory(self, backup):
        files = {}
        for path in sorted(backup.rglob("*")):
            require(not path.is_symlink(), "unexpected backup symlink")
            if path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        digest.update(block)
                files[str(path.relative_to(backup))] = {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}
        require(files, "empty native bootstrap backup")
        return files

    def bootstrap(self, data):
        """Use canonical embedded init and full native restore before any listener."""
        self.no_tcp_listeners()
        client = self.root / "bootstrap"
        env = self.env("root", client)
        for key in list(env):
            if key.startswith("BEADS_DOLT_SERVER_") and key != "BEADS_DOLT_SERVER_DATABASE":
                del env[key]
        env.pop("BEADS_DOLT_PASSWORD")
        env["BEADS_DOLT_SERVER_MODE"] = "0"
        repo = client / "repo"
        self.run([self.args.git, "init", "-q", "--initial-branch=main"], env, repo, "bootstrap-git")
        for key, value in [("user.name", "Beads SQL fixture"), ("user.email", "sql-fixture@example.invalid")]:
            self.run([self.args.git, "config", key, value], env, repo, "bootstrap-git-identity")
        def bd(arguments, name):
            return self.run([self.args.bd, "--sandbox", *arguments], env, repo, name)
        bd(["init", "--server=false", "--database", DATABASE, "--prefix", DATABASE_PREFIX, "--skip-hooks", "--skip-agents", "--non-interactive", "--quiet"], "offline-initialize")
        metadata = json.loads((repo / ".beads/metadata.json").read_text())
        require(metadata.get("dolt_mode") == "embedded", "bootstrap did not use embedded mode")
        _, output, _ = bd(["create", "Offline bootstrap canary", "--type", "task", "--json"], "offline-canary")
        canary = decode_json(output)["id"]
        backup = self.root / "bootstrap-backup"
        backup.mkdir(mode=0o700)
        bd(["backup", "init", str(backup)], "offline-backup-init")
        bd(["backup", "sync"], "offline-backup-sync")
        original = repo / ".beads/embeddeddolt" / DATABASE
        require(original.is_dir() and (original / ".dolt").is_dir(), "native embedded database missing")
        before = self.catalog(lambda query: self.local_sql(original, query))
        server_metadata = {**metadata, "dolt_mode": "server", "dolt_server_tls": True,
                           "dolt_server_host": "127.0.0.1", "dolt_server_port": self.port, "dolt_server_user": "root"}
        verify_identity(server_metadata, before["project_id"])
        inventory = self.backup_inventory(backup)
        require(not any(data.iterdir()), "server data directory must be empty before restore")
        self.run([self.args.dolt, "backup", "restore", "file://" + str(backup), DATABASE], self.env("root"), data, "offline-native-restore")
        restored = self.catalog(lambda query: self.local_sql(data / DATABASE, query))
        require(restored == before, "offline restored project/schema/tables/history/branches differ")
        require(self.backup_inventory(backup) == inventory, "native backup artifact changed during restore")
        self.no_tcp_listeners()
        self.report["bootstrap"] = {"mode": "embedded-native-backup", "canary_id": canary,
                                    "catalog": before, "backup_files": inventory, "artifact_unchanged": True,
                                    "no_tcp_listeners_at_checks": True}
        self.stage("offline canonical initialization and exact native restoration")
        return server_metadata, before, canary

    def setup(self):
        interfaces = sorted(name for _, name in socket.if_nameindex())
        require(interfaces == ["lo"], f"test requires a private loopback-only network namespace: {interfaces}")
        self.report["interfaces"] = interfaces
        for label, binary in [("beads", self.args.bd), ("dolt", self.args.dolt)]:
            path = Path(binary).resolve(strict=True)
            require(str(path).startswith("/nix/store/"), "test requires explicit immutable Nix binaries")
            self.report[label] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            self.port = reserved.getsockname()[1]
        self.report["endpoint"] = f"127.0.0.1:{self.port}"
        env = self.env("root")
        # This isolated process owns all bootstrap authority. It has no Git key or remote.
        env["BEADS_DOLT_PASSWORD"] = ""
        self.run([self.args.openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Beads fixture CA",
                  "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                  "-keyout", "ca.key", "-out", "ca.pem"], env, self.root, "ca")
        self.run([self.args.openssl, "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1", "-keyout", "server.key", "-out", "server.csr"], env, self.root, "certificate-request")
        (self.root / "extensions").write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nsubjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n")
        self.run([self.args.openssl, "x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-days", "1", "-extfile", "extensions", "-out", "server.pem"], env, self.root, "certificate")
        self.run([self.args.openssl, "verify", "-x509_strict", "-purpose", "sslserver", "-verify_ip", "127.0.0.1", "-CAfile", "ca.pem", "server.pem"], env, self.root, "certificate-strict-verification")
        for key in ["ca.key", "server.key"]:
            (self.root / key).chmod(0o600)
        for setting in ["metrics.disabled", "versioncheck.disabled"]:
            self.run([self.args.dolt, "config", "--global", "--add", setting, "true"], env, self.root, "disable-optional-network")
        for key, value in [("user.name", "Beads SQL fixture"), ("user.email", "sql-fixture@example.invalid")]:
            self.run([self.args.dolt, "config", "--global", "--add", key, value], env, self.root, "private-dolt-identity")
        _, version, _ = self.run([self.args.dolt, "version"], env, self.root, "dolt-version")
        require("dolt version 2.1.0" in version, "unexpected Dolt version")
        _, version, _ = self.run([self.args.bd, "version"], env, self.root, "beads-version")
        require("bd version 1.2.2" in version, "unexpected Beads version")
        data = self.root / "server-data"
        data.mkdir(mode=0o700)
        metadata, bootstrap_catalog, bootstrap_canary = self.bootstrap(data)
        config = {
            "log_level": "warning", "data_dir": str(data), "cfg_dir": str(self.root / "server-config"),
            "behavior": {"autocommit": True, "dolt_transaction_commit": False},
            "listener": {"host": "127.0.0.1", "port": self.port, "max_connections": 32,
                         "read_timeout_millis": 10000, "write_timeout_millis": 10000,
                         "tls_key": str(self.root / "server.key"), "tls_cert": str(self.root / "server.pem"), "require_secure_transport": True},
            "metrics": {"port": -1},
        }
        (self.root / "server.yaml").write_text(json.dumps(config))
        self.listener_allowed = True
        self.server, _, _ = self.launch([self.args.dolt, "sql-server", "--config", str(self.root / "server.yaml")], env, self.root, "server")
        ready_deadline = time.monotonic() + 30
        while True:
            try:
                self.sql("SELECT 1", database=None, password="")
                break
            except Exception as error:
                self.report["readiness_last_error"] = self.redact(f"{type(error).__name__}: {error}")
                require(self.server.poll() is None, "SQL server exited before authenticated readiness")
                require(time.monotonic() < ready_deadline, f"SQL readiness deadline: {self.report['readiness_last_error']}")
                time.sleep(0.1)
        self.sql("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (self.passwords["root"],), database=None, password="")
        require(self.sql("SELECT 1") == ((1,),), "password-authenticated SQL readiness failed")
        require(self.catalog(lambda query: self.sql(query, records=True)) == bootstrap_catalog, "TLS server catalog differs from offline native source")
        require(self.sql("SELECT title FROM issues WHERE id=%s", (bootstrap_canary,))[0][0] == "Offline bootstrap canary", "bootstrap issue absent over TLS")
        self.stage("verified TLS authentication and unchanged native catalog")
        for user in ["root", "reader", "alice", "bob"]:
            user_env = self.env(user)
            repo = self.root / user / "repo"
            self.run([self.args.git, "init", "-q", "--initial-branch=main"], user_env, repo, "git-init")
            self.run([self.args.git, "config", "user.name", "Beads SQL fixture"], user_env, repo, "git-name")
            self.run([self.args.git, "config", "user.email", "sql-fixture@example.invalid"], user_env, repo, "git-email")
        root_beads = self.root / "root/repo/.beads"
        root_beads.mkdir(mode=0o700)
        (root_beads / "metadata.json").write_text(json.dumps(metadata))
        project = self.sql("SELECT value FROM metadata WHERE `key`='_project_id'")[0][0]
        self.report["project_id"] = verify_identity(metadata, project)
        for user in ["reader", "alice", "bob"]:
            self.sql("CREATE USER %s@%s IDENTIFIED BY %s", (user, "%", self.passwords[user]))
            privileges = "SELECT" if user == "reader" else "SELECT, INSERT, UPDATE, DELETE, EXECUTE"
            self.sql(f"GRANT {privileges} ON `{DATABASE}`.* TO %s@%s", (user, "%"))
            beads_dir = self.root / user / "repo/.beads"
            beads_dir.mkdir(mode=0o700)
            (beads_dir / "metadata.json").write_text(json.dumps(metadata))
            # Metadata is checked against SQL before any worker writable open.
            verify_identity(metadata, self.sql("SELECT value FROM metadata WHERE `key`='_project_id'", user=user)[0][0])
        self.stage("authenticated TLS bootstrap and project preflight")

    def exercise(self):
        status, output, _ = self.bd("alice", ["create", "Concurrent claim fixture", "--type", "task", "--json"], name="create")
        issue_id = decode_json(output)["id"]
        self.bd("reader", ["--readonly", "show", issue_id, "--json"], name="reader-positive")
        status, _, error = self.bd("reader", ["update", issue_id, "--title", "forbidden reader mutation"], expect=None, name="reader-negative")
        verify_rejection(status, error, ["denied", "privilege", "permission"], "reader mutation")
        require(self.sql("SELECT title FROM issues WHERE id=%s", (issue_id,))[0][0] == "Concurrent claim fixture", "reader changed issue")
        self.stage("writer command and read-only SQL account")

        for label, extra, patterns in [
            ("wrong-password", {"BEADS_DOLT_PASSWORD": "wrong-synthetic-password"}, ["access denied", "authentication"]),
            ("plaintext", {"BEADS_DOLT_SERVER_TLS": "0"}, ["secure transport", "TLS", "SSL", "insecure transport"]),
        ]:
            status, _, error = self.bd("alice", ["--readonly", "list", "--json"], expect=None, env_extra=extra, name=label)
            verify_rejection(status, error, patterns, label)
        status, _, error = self.bd("alice", ["--readonly", "list", "--json"], expect=None,
                               env_extra={"SSL_CERT_FILE": str(self.root / "alice/empty-ca-dir/missing.pem")}, name="untrusted-ca")
        verify_rejection(status, error, ["unknown authority", "certificate", "x509"], "untrusted CA")
        status, _, error = self.bd("alice", ["--readonly", "list", "--json"], expect=None,
                                   env_extra={"BEADS_DOLT_SERVER_HOST": "localhost"}, name="wrong-certificate-name")
        verify_rejection(status, error, ["certificate", "x509"], "wrong certificate name")
        self.stage("authentication plaintext and CA negative controls")

        actors = ["alice", "bob"]
        barrier = threading.Barrier(2)
        def claim(actor):
            barrier.wait(timeout=5)
            return self.bd(actor, ["update", issue_id, "--claim", "--json"], expect=None, name=f"claim-{actor}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, actors))
        _, output, _ = self.bd("reader", ["--readonly", "show", issue_id, "--json"], name="claim-result")
        self.report["claim_commands"] = [{"actor": actor, "exit_code": status, "stdout": stdout, "stderr": stderr}
                                         for actor, (status, stdout, stderr) in zip(actors, results)]
        winner = verify_claim_commands(results, decode_json(output)[0], actors, allow_serialization=True)
        loser = next(actor for actor in actors if actor != winner)
        initial_rejection = classify_claim_rejection(results[actors.index(loser)], issue_id, winner, allow_serialization=True)
        events = self.sql("SELECT COUNT(*) FROM events WHERE issue_id=%s AND event_type='claimed'", (issue_id,))[0][0]
        require(events == 1, f"expected one claim event, found {events}")
        retry = self.bd(loser, ["update", issue_id, "--claim", "--json"], expect=None, name="claim-loser-retry")
        self.report["claim_loser_retry"] = {"actor": loser, "initial_rejection": initial_rejection,
                                            "exit_code": retry[0], "stdout": retry[1], "stderr": retry[2]}
        require(classify_claim_rejection(retry, issue_id, winner) == "already-claimed", "loser retry did not resolve to domain refusal")
        require(self.sql("SELECT assignee, status FROM issues WHERE id=%s", (issue_id,)) == ((winner, "in_progress"),), "loser retry changed durable claim")
        require(self.sql("SELECT COUNT(*) FROM events WHERE issue_id=%s AND event_type='claimed'", (issue_id,))[0][0] == events, "loser retry changed claim event count")
        self.bd(winner, ["update", issue_id, "--claim", "--json"], name="claim-idempotent")
        require(self.sql("SELECT assignee, status FROM issues WHERE id=%s", (issue_id,)) == ((winner, "in_progress"),), "same-actor retry changed durable claim")
        require(self.sql("SELECT COUNT(*) FROM events WHERE issue_id=%s AND event_type='claimed'", (issue_id,))[0][0] == events, "same-actor retry duplicated claim event")
        self.report["claim"] = {"issue_id": issue_id, "statuses": [result[0] for result in results], "winner": winner, "events": events,
                                "initial_loser_rejection": initial_rejection, "explicit_loser_retry": "already-claimed"}
        self.stage("two distinct actors and same-actor retry")

        self.sql("CREATE TABLE backup_canary (id INT PRIMARY KEY, value TEXT)")
        self.sql("INSERT INTO backup_canary VALUES (1,'before-bd-backup')")
        backup = self.root / "native-backup"
        backup.mkdir(mode=0o700)
        self.bd("root", ["backup", "init", str(backup)], name="backup-init")
        before_backup = set(self.sql("SELECT commit_hash FROM dolt_log"))
        require(self.sql("SELECT table_name FROM dolt_status WHERE table_name='backup_canary'"), "pre-backup canary is not dirty")
        self.bd("root", ["backup", "sync"], name="backup-sync")
        # bd backup sync explicitly commits first; do not call this an uncommitted backup.
        history = sorted(self.sql("SELECT commit_hash FROM dolt_log"))
        require(before_backup < set(history), "bd backup did not add a native commit")
        require(not self.sql("SELECT table_name FROM dolt_status WHERE table_name='backup_canary'"), "bd backup left canary uncommitted")
        self.sql("CALL DOLT_BACKUP('restore', %s, 'beads_restored')", ("file://" + str(backup),))
        require(self.sql("SELECT value FROM metadata WHERE `key`='_project_id'", database="beads_restored")[0][0] == self.report["project_id"], "restored project differs")
        require(self.sql("SELECT assignee FROM issues WHERE id=%s", (issue_id,), database="beads_restored")[0][0] == winner, "restored claim differs")
        require(self.sql("SELECT value FROM backup_canary WHERE id=1", database="beads_restored")[0][0] == "before-bd-backup", "data absent from bd native backup")
        require(not self.sql("SELECT table_name FROM dolt_status WHERE table_name='backup_canary'", database="beads_restored"), "bd restored canary is not committed")
        require(sorted(self.sql("SELECT commit_hash FROM dolt_log", database="beads_restored")) == history, "restored native history differs")
        self.sql("UPDATE backup_canary SET value='uncommitted-working-set' WHERE id=1")
        require(self.sql("SELECT table_name FROM dolt_status WHERE table_name='backup_canary'"), "working-set positive control is not dirty")
        self.sql("CALL DOLT_BACKUP('sync', 'default')")
        require(sorted(self.sql("SELECT commit_hash FROM dolt_log")) == history, "SQL backup unexpectedly committed working set")
        self.sql("CALL DOLT_BACKUP('restore', %s, 'beads_working_restored')", ("file://" + str(backup),))
        require(self.sql("SELECT value FROM backup_canary WHERE id=1", database="beads_working_restored")[0][0] == "uncommitted-working-set", "working set absent from SQL native backup")
        require(self.sql("SELECT table_name FROM dolt_status WHERE table_name='backup_canary'", database="beads_working_restored"), "restored working set not dirty")
        require(sorted(self.sql("SELECT commit_hash FROM dolt_log", database="beads_working_restored")) == history, "SQL native backup history differs")
        self.report["backup"] = {"history_commits": len(history), "bd_sync_precommits": True, "sql_backup_working_set_restored": True}
        self.stage("native backup and fresh database restore")
        for user in self.passwords:
            repo = self.root / user / "repo"
            require(not (repo / "AGENTS.md").exists(), "unexpected repository instructions")
            require(not (repo / ".git/hooks/pre-commit").exists(), "unexpected repository hook")
            require(not (repo / ".beads/embeddeddolt").exists(), "unexpected embedded fallback")
            _, remotes, _ = self.run([self.args.git, "remote"], self.env(user), repo, "no-git-remote")
            require(not remotes.strip(), "unexpected Git publication remote")
            for path in repo.rglob("*"):
                if path.is_file() and path.stat().st_size <= 4 * 1024**2:
                    data = path.read_bytes()
                    require(not any(password.encode() in data for password in self.passwords.values()),
                            f"synthetic SQL credential persisted in client repository: {path.relative_to(repo)}")
        self.stage("client repositories contain no SQL credentials or Git publication remote")

    def close(self):
        # Cleanup does not call work-quota checks; the original failure survives.
        self.stop_monitor.set()
        self.meter.join(timeout=2)
        with self.lock:
            children = list(self.children)
        self.report["cleanup"] = stop_children(children, self.server)
        self.report["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        require(not self.report["cleanup"]["forced_pids"], f"forced child cleanup: {self.report['cleanup']}")
        require(not self.report["cleanup"]["errors"] and self.report["cleanup"]["all_registered_children_reaped"], f"child cleanup failed: {self.report['cleanup']}")
        require(self.failure is None, self.failure or "resource monitor failed")
        if self.server is not None:
            require(self.server.returncode == 0, f"server did not stop normally: {self.server.returncode}")


def main():
    parser = argparse.ArgumentParser()
    for name in ["bd", "dolt", "git", "openssl", "scratch", "output"]:
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    fixture = Fixture(args)
    try:
        fixture.setup()
        fixture.exercise()
        fixture.report["status"] = "passed"
    except Exception as error:
        fixture.report["status"] = "failed"
        fixture.report["error"] = fixture.redact(error)
    finally:
        try:
            fixture.close()
        except Exception as error:
            fixture.report["status"] = "failed"
            fixture.report["cleanup_error"] = fixture.redact(error)
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        for path in fixture.log_files:
            if path.exists():
                with path.open(errors="replace") as log:
                    contents = fixture.redact(log.read(4 * 1024**2))
                (output / path.name).write_text(contents)
                if fixture.report["status"] != "passed":
                    print(f"--- synthetic child log {path.name} (bounded) ---\n{contents[-32768:]}", flush=True)
        (output / "report.json").write_text(json.dumps(fixture.report, indent=2) + "\n")
        print(json.dumps(fixture.report, indent=2), flush=True)
    return 0 if fixture.report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
