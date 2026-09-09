"""Negative controls for the archive inspection oracle; no guest execution."""

import contextlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from check_archive import check, normalize


STORE = "/nix/store/00000000000000000000000000000000-busybox-test"
AGGREGATE = "/nix/store/11111111111111111111111111111111-fake-nss"


def archive(path, *, shell=True, tmp_mode=0o1777, extra=None, aggregate_link=None, aggregate_config=False):
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as target:
        for directory in [".", "tmp", "/nix", "/nix/store", STORE, STORE + "/bin", "etc", "var"]:
            member = tarfile.TarInfo(directory)
            member.type = tarfile.DIRTYPE
            member.mode = tmp_mode if directory == "tmp" else 0o755
            target.addfile(member)
        for name in ["etc/passwd", "etc/group", "etc/nsswitch.conf"] + ([STORE + "/bin/sh"] if shell else []):
            member = tarfile.TarInfo(name)
            target.addfile(member, io.BytesIO())
        member = tarfile.TarInfo("bin")
        member.type = tarfile.SYMTYPE
        member.linkname = STORE + "/bin"
        target.addfile(member)
        if extra:
            target.addfile(tarfile.TarInfo(extra), io.BytesIO())
        if aggregate_link:
            member = tarfile.TarInfo("var/broken")
            member.type = tarfile.SYMTYPE
            member.linkname = aggregate_link
            target.addfile(member)
    config = {
        "created": "1970-01-01T00:00:01+00:00",
        "config": {"Cmd": ["/bin/sh"], "WorkingDir": "/tmp", "Env": ["PATH=/bin"]},
        "rootfs": {"type": "layers", "diff_ids": ["sha256:test"]},
    }
    if aggregate_config:
        config["config"]["Labels"] = {"aggregate": AGGREGATE}
    manifest = [{"Config": "config.json", "Layers": ["layer.tar"], "RepoTags": ["guest-minimal:test"]}]
    with tarfile.open(path, "w:gz") as target:
        for name, data in {
            "manifest.json": json.dumps(manifest).encode(),
            "config.json": json.dumps(config).encode(),
            "layer.tar": layer.getvalue(),
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            target.addfile(member, io.BytesIO(data))


class ArchiveContract(unittest.TestCase):
    def run_case(self, *, store_paths=(STORE,), aggregation_roots=(), **kwargs):
        with tempfile.TemporaryDirectory(prefix="guest-archive-test-") as root:
            path = Path(root) / "image.tar.gz"
            archive(path, **kwargs)
            with contextlib.redirect_stdout(io.StringIO()):
                check(path, store_paths, aggregation_roots)

    def test_valid_archive_and_intermediate_symlink(self):
        self.run_case()

    def test_missing_shell_target(self):
        with self.assertRaisesRegex(AssertionError, "missing image path"):
            self.run_case(shell=False)

    def test_flattened_aggregation_root(self):
        self.run_case(store_paths=[STORE, AGGREGATE], aggregation_roots=[AGGREGATE])

    def test_missing_runtime_root_is_not_an_aggregate(self):
        with self.assertRaisesRegex(AssertionError, "closure root absent"):
            self.run_case(store_paths=[STORE, AGGREGATE])

    def test_absent_aggregate_absolute_and_relative_links_rejected(self):
        for target in [AGGREGATE + "/etc/passwd", ".." + AGGREGATE + "/etc/passwd"]:
            with self.subTest(target=target), self.assertRaisesRegex(AssertionError, "link references absent aggregate"):
                self.run_case(store_paths=[STORE, AGGREGATE], aggregation_roots=[AGGREGATE], aggregate_link=target)

    def test_absent_aggregate_config_reference_rejected(self):
        with self.assertRaisesRegex(AssertionError, "configuration references absent aggregate"):
            self.run_case(store_paths=[STORE, AGGREGATE], aggregation_roots=[AGGREGATE], aggregate_config=True)

    def test_missing_sticky_tmp(self):
        with self.assertRaises(AssertionError):
            self.run_case(tmp_mode=0o755)

    def test_implicit_engine_rejected(self):
        with self.assertRaisesRegex(AssertionError, "unexpected guest dependency"):
            self.run_case(extra="nix/store/00000000000000000000000000000000-nix-2.35/bin/nix")

    def test_whiteout_rejected(self):
        with self.assertRaisesRegex(AssertionError, "unexpected whiteout"):
            self.run_case(extra=".wh.etc")

    def test_escaping_paths_rejected(self):
        for name in ["../outside", "/absolute", "nested/../../outside"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                normalize(name)

    def test_other_absolute_paths_rejected(self):
        for name in ["/etc/shadow", "/nix/private", "/nix/store/../private"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.run_case(extra=name)


if __name__ == "__main__":
    unittest.main()
