"""Describe an immutable admitted closure without copying its payload bytes."""

import json
from pathlib import Path
import re
import stat
import sys

MAX_INVENTORY_BYTES = 4 * 1024 * 1024
MAX_REGISTRATION_BYTES = 64 * 1024 * 1024
MAX_PATHS = 100_000
STORE_PATH = re.compile(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9+._?=-]+")


def decode_paths(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_INVENTORY_BYTES:
        raise ValueError("closure inventory exceeds the byte budget or is empty")
    if not data.endswith(b"\n") or b"\r" in data:
        raise ValueError("closure inventory requires canonical newline records")
    paths = data.decode("utf-8").splitlines()
    if len(paths) > MAX_PATHS or len(set(paths)) != len(paths):
        raise ValueError("closure inventory repeats paths or exceeds the path budget")
    if any(STORE_PATH.fullmatch(path) is None for path in paths):
        raise ValueError("closure inventory contains a noncanonical store root")
    return paths


def make_manifest(roots, paths, lstat):
    if not 1 <= len(roots) <= 64 or not set(roots).issubset(paths):
        raise ValueError("all explicit roots must be members of the admitted closure")
    entries = []
    for path in sorted(paths):
        mode = lstat(path).st_mode
        if stat.S_ISDIR(mode):
            kind = "directory"
        elif stat.S_ISREG(mode):
            kind = "regular"
        else:
            raise ValueError("closure roots must be directories or regular files; symlinks are not followed")
        entries.append({"path": path, "kind": kind})
    return {
        "schema_version": 1,
        "store_dir": "/nix/store",
        "roots": roots,
        "entries": entries,
        "store_paths_file": "store-paths",
        "registration_file": "registration",
    }


def read_bounded(path, limit):
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("closure metadata is empty or exceeds its byte budget")
    return data


def export(closure, roots_file, destination):
    paths = decode_paths(read_bounded(closure / "store-paths", MAX_INVENTORY_BYTES))
    roots = decode_paths(read_bounded(roots_file, MAX_INVENTORY_BYTES))
    registration = read_bounded(closure / "registration", MAX_REGISTRATION_BYTES)
    manifest = make_manifest(roots, paths, lambda path: Path(path).lstat())
    # Validate every member before creating any output. Existing destinations
    # are rejected; the caller owns the lifetime of the resulting derivation.
    destination.mkdir()
    (destination / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    (destination / "store-paths").write_text("".join(entry["path"] + "\n" for entry in manifest["entries"]))
    (destination / "roots").write_bytes(b"".join(root.encode() + b"\n" for root in roots))
    (destination / "registration").write_bytes(registration)
    for path in destination.iterdir():
        path.chmod(0o444)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise ValueError("expected closure metadata, explicit roots and output directory")
    export(*(Path(value) for value in sys.argv[1:]))
