"""Small, side-effect-free assertions shared by the native SQL fixture."""

import json
import re
from pathlib import Path


DATABASE = "beads_contract"
DATABASE_PREFIX = "sqlcheck"
BEADS_VERSION = "1.2.2"
DOLT_VERSION = "2.1.0"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def owned_path(root, value):
    """Require a path inside the explicit fixture root, without symlink aliases."""
    root = Path(root)
    value = Path(value)
    require(root.is_absolute() and value.is_absolute(), "paths must be absolute")
    require(root.resolve() == root, "fixture root must not contain symlinks")
    require(value.resolve() == value, "fixture path must not contain symlinks")
    require(value != root and root in value.parents, "path escapes fixture root")
    return value


def validate_endpoint(host, port):
    require(host == "127.0.0.1", "native fixture requires IPv4 loopback")
    require(type(port) is int and 1024 <= port <= 65535, "invalid fixture port")
    return host, port


def client_env(root, client, path, host, port, user, password, ca):
    """Construct a complete environment without inheriting credentials or config."""
    validate_endpoint(host, port)
    client = owned_path(root, client)
    ca = owned_path(root, ca)
    require(re.fullmatch(r"[a-z][a-z0-9_]{0,31}", user), "invalid SQL user")
    require(password and "\n" not in password, "invalid synthetic password")
    return {
        "PATH": path,
        "HOME": str(client / "home"),
        "TMPDIR": str(client / "tmp"),
        "XDG_CONFIG_HOME": str(client / "config"),
        "XDG_CACHE_HOME": str(client / "cache"),
        "XDG_DATA_HOME": str(client / "data"),
        "DOLT_ROOT_PATH": str(client / "dolt-global"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C.UTF-8",
        "BEADS_DIR": str(client / "repo" / ".beads"),
        "BEADS_DOLT_SERVER_MODE": "1",
        "BEADS_DOLT_SHARED_SERVER": "0",
        "BEADS_DOLT_AUTO_START": "0",
        "BEADS_DOLT_SERVER_HOST": host,
        "BEADS_DOLT_SERVER_PORT": str(port),
        "BEADS_DOLT_SERVER_DATABASE": DATABASE,
        "BEADS_DOLT_SERVER_USER": user,
        "BEADS_DOLT_PASSWORD": password,
        "BEADS_DOLT_SERVER_TLS": "1",
        "BEADS_ACTOR": user,
        "BD_DOLT_AUTO_COMMIT": "off",
        "BD_DISABLE_METRICS": "1",
        "BD_DISABLE_EVENT_FLUSH": "1",
        "SSL_CERT_FILE": str(ca),
        "SSL_CERT_DIR": str(client / "empty-ca-dir"),
    }


def verify_identity(metadata, remote_id):
    require(isinstance(metadata, dict), "metadata must be an object")
    local_id = metadata.get("project_id")
    require(isinstance(local_id, str) and local_id, "missing local project ID")
    require(isinstance(remote_id, str) and remote_id, "missing server project ID")
    require(local_id == remote_id, "project identity mismatch")
    require(metadata.get("dolt_database") == DATABASE, "unexpected database")
    require(metadata.get("dolt_mode") == "server", "unexpected storage mode")
    return local_id


def verify_claim(results, issue, actors):
    """A claim passes only with one acknowledged winner and matching durable state."""
    require(len(results) == len(actors) and len(set(actors)) == len(actors), "distinct actors required")
    winners = [actor for actor, status in zip(actors, results) if status == 0]
    require(len(winners) == 1, "claim did not produce exactly one acknowledged winner")
    require(issue["status"] == "in_progress", "claimed issue status differs")
    require(issue["assignee"] == winners[0], "durable assignee differs from winner")
    return winners[0]


def classify_claim_rejection(result, issue_id, winner, allow_serialization=False):
    status, stdout, stderr = result
    require(status != 0 and not stdout.strip(), "losing claim unexpectedly acknowledged a result")
    domain = rf"(?:Error claiming {re.escape(issue_id)}: )?issue already claimed by {re.escape(winner)}"
    if re.fullmatch(domain, stderr.strip()):
        return "already-claimed"
    serialization = (f"Error claiming {issue_id}: dolt commit: Error 1213 (40001): serialization failure: "
                     "this transaction conflicts with a committed transaction from another client, try restarting transaction.")
    if allow_serialization and stderr.strip() == serialization:
        return "serialization-rollback"
    raise AssertionError(f"losing claim failed for an unclassified reason: {stderr[-4000:]}")


def verify_claim_commands(results, issue, actors, allow_serialization=False):
    winner = verify_claim([result[0] for result in results], issue, actors)
    for actor, (status, stdout, stderr) in zip(actors, results):
        if actor == winner:
            payload = decode_json(stdout)
            require(isinstance(payload, list) and len(payload) == 1, "missing acknowledged claim record")
            require(payload[0]["id"] == issue["id"] and payload[0]["assignee"] == winner
                    and payload[0]["status"] == "in_progress", "acknowledged claim differs from durable result")
        else:
            classify_claim_rejection((status, stdout, stderr), issue["id"], winner, allow_serialization)
    return winner


def decode_json(data):
    value = json.loads(data)
    require(not (isinstance(value, dict) and value.get("error")), "CLI returned an error object")
    return value


def verify_rejection(status, stderr, patterns, label):
    require(status != 0, f"{label} unexpectedly succeeded")
    require(any(re.search(pattern, stderr, re.IGNORECASE) for pattern in patterns),
            f"{label} failed for an unclassified reason: {stderr[-4000:]}")
