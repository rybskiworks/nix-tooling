"""Inspect a dockerTools archive without extracting or executing its contents."""

import json
import hashlib
import os
import posixpath
import re
import sys
import tarfile
from datetime import datetime, timezone


FORBIDDEN = re.compile(
    r"^nix/store/[^/]+-(?:nix-[0-9]|determinate|beads-|workestrate-|"
    r"microsandbox-|codex-|prime-)"
)


def normalize(name, *, allow_absolute_store=False):
    if name.startswith("/") and allow_absolute_store:
        # Pinned dockerTools emits absolute store members plus these parents.
        if ".." in name.split("/") or not (
            name.rstrip("/") in {"/nix", "/nix/store"}
            or re.fullmatch(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[^/]+(?:/.*)?", name)
        ):
            raise ValueError(f"unexpected absolute archive path: {name}")
        name = name[1:]
    result = posixpath.normpath(name)
    if result.startswith("/") or result == ".." or result.startswith("../"):
        raise ValueError(f"unsafe archive path: {name}")
    return result


def link_target(name, member):
    target = member.linkname
    if member.issym() and not target.startswith("/"):
        target = posixpath.join(posixpath.dirname(name), target)
    return normalize(target.lstrip("/"))


def check(archive_path, store_paths, aggregation_roots=()):
    entries = {}
    with tarfile.open(archive_path, "r:*") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        assert len(manifest) == 1, "expected exactly one image"
        assert manifest[0]["RepoTags"] == ["guest-minimal:test"]
        config = json.load(archive.extractfile(manifest[0]["Config"]))
        assert datetime.fromisoformat(config["created"]) == datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        assert config["config"]["Cmd"] == ["/bin/sh"]
        assert config["config"]["WorkingDir"] == "/tmp"
        assert config["config"]["Env"] == ["PATH=/bin"]
        assert config["rootfs"]["type"] == "layers"
        assert len(config["rootfs"]["diff_ids"]) == len(manifest[0]["Layers"])
        assert manifest[0]["Layers"], "image has no filesystem layers"
        for layer in manifest[0]["Layers"]:
            # Streaming keeps peak memory independent of a layer's payload size.
            with tarfile.open(fileobj=archive.extractfile(layer), mode="r|*") as stream:
                for member in stream:
                    name = normalize(member.name, allow_absolute_store=True)
                    assert not FORBIDDEN.match(name), f"unexpected guest dependency: {name}"
                    assert not posixpath.basename(name).startswith(".wh."), "unexpected whiteout"
                    entries[name] = member

    assert entries["tmp"].isdir() and entries["tmp"].mode & 0o7777 == 0o1777
    assert set(aggregation_roots) <= set(store_paths), "unknown aggregation root"
    for path in store_paths:
        if path in aggregation_roots and path.lstrip("/") not in entries:
            # symlinkJoin contents can be flattened to their actual leaf roots.
            assert path not in json.dumps(config), "configuration references absent aggregate"
            for name, member in entries.items():
                if member.issym() or member.islnk():
                    target = link_target(name, member)
                    assert not (
                        target == path[1:] or target.startswith(path[1:] + "/")
                    ), "image link references absent aggregate"
            continue
        assert path.lstrip("/") in entries, f"closure root absent from image: {path}"
    assert "nix/var/nix/db/db.sqlite" not in entries, "implicit Nix database"
    assert "run/current-system" not in entries, "implicit NixOS profile"

    def resolve(path):
        # Resolve intermediate directory links as well as the leaf itself.
        for _ in range(40):
            parts = path.split("/")
            for index in range(1, len(parts) + 1):
                prefix = "/".join(parts[:index])
                member = entries.get(prefix)
                assert member is not None, f"missing image path: {prefix}"
                if member.issym() or member.islnk():
                    path = normalize(posixpath.join(link_target(prefix, member), *parts[index:]))
                    break
            else:
                return member
        raise AssertionError("image symlink cycle")

    assert resolve("bin/sh").isfile(), "shell target is not a file"
    for name in ["passwd", "group", "nsswitch.conf"]:
        assert resolve("etc/" + name).isfile(), "NSS target is not a file"
    omitted = sum(path.lstrip("/") not in entries for path in aggregation_roots)
    with open(archive_path, "rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        size = os.fstat(source.fileno()).st_size
    print(json.dumps({
        "layers": len(manifest[0]["Layers"]),
        "runtime_store_paths": len(store_paths) - omitted,
        "flattened_aggregation_roots": omitted,
        "archive_sha256": digest,
        "archive_size_bytes": size,
    }, sort_keys=True))


if __name__ == "__main__":
    with open(sys.argv[2], encoding="utf-8") as source:
        check(sys.argv[1], source.read().splitlines(), sys.argv[3:])
