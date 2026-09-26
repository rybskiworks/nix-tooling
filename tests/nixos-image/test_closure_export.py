"""Portable admission and failure controls for closure export metadata."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get("CLOSURE_EXPORT_SCRIPT", Path(__file__).parents[2] / "lib/guest/closure-export.py"))
SPEC = importlib.util.spec_from_file_location("guest_closure_export", SOURCE)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def root(name):
    return "/nix/store/" + "0" * 32 + "-" + name


def metadata(mode):
    return os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))


class ClosureExportTests(unittest.TestCase):
    def test_directory_and_regular_members_are_sorted_without_following(self):
        paths = [root("z-file"), root("a-directory")]
        seen = []

        def inspect(path):
            seen.append(path)
            return metadata(stat.S_IFREG if path.endswith("z-file") else stat.S_IFDIR)

        manifest = exporter.make_manifest([paths[1]], paths, inspect)
        self.assertEqual(seen, sorted(paths))
        self.assertEqual(manifest["entries"], [
            {"path": paths[1], "kind": "directory"},
            {"path": paths[0], "kind": "regular"},
        ])
        self.assertEqual(manifest["roots"], [paths[1]])
        self.assertEqual(manifest["store_dir"], "/nix/store")
        self.assertEqual(manifest["registration_file"], "registration")

    def test_symlink_and_special_members_fail_closed(self):
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                exporter.make_manifest([root("a")], [root("a")], lambda _: metadata(mode))

    def test_missing_or_unadmitted_roots_are_rejected(self):
        for roots in ([], [root("missing")], [root("a")] * 65):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                exporter.make_manifest(roots, [root("a")], lambda _: metadata(stat.S_IFDIR))

    def test_inventory_is_canonical_and_unique(self):
        for value in (b"", b"\n", root("a").encode(), (root("a") + "\r\n").encode(),
                      (root("a") + "\n" + root("a") + "\n").encode(),
                      (root("a") + "/bin\n").encode(), b"/etc/passwd\n", b"relative\n",
                      (root("a") + "\x00\n").encode(), b"\xff\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                exporter.decode_paths(value)

    def test_inventory_byte_and_path_budgets(self):
        with patch.object(exporter, "MAX_INVENTORY_BYTES", 2), self.assertRaises(ValueError):
            exporter.decode_paths((root("a") + "\n").encode())
        with patch.object(exporter, "MAX_PATHS", 1), self.assertRaises(ValueError):
            exporter.decode_paths((root("a") + "\n" + root("b") + "\n").encode())

    def fixture(self, directory):
        closure = directory / "closure"
        closure.mkdir()
        (closure / "store-paths").write_text(root("a") + "\n")
        (closure / "registration").write_bytes(b"fixture registration\n")
        roots = directory / "roots"
        roots.write_text(root("a") + "\n")
        return closure, roots, directory / "output"

    def test_output_has_only_immutable_metadata_and_retains_registration(self):
        with tempfile.TemporaryDirectory() as temporary:
            closure, roots, output = self.fixture(Path(temporary))
            with patch.object(Path, "lstat", return_value=metadata(stat.S_IFDIR)):
                exporter.export(closure, roots, output)
            self.assertEqual(sorted(path.name for path in output.iterdir()),
                             ["manifest.json", "registration", "roots", "store-paths"])
            self.assertEqual((output / "registration").read_bytes(), (closure / "registration").read_bytes())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual([entry["path"] for entry in manifest["entries"]],
                             (output / "store-paths").read_text().splitlines())
            for path in output.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)

    def test_bad_member_fails_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            closure, roots, output = self.fixture(Path(temporary))
            with patch.object(Path, "lstat", return_value=metadata(stat.S_IFLNK)), self.assertRaises(ValueError):
                exporter.export(closure, roots, output)
            self.assertFalse(output.exists())

    def test_existing_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            closure, roots, output = self.fixture(Path(temporary))
            output.mkdir()
            marker = output / "keep"
            marker.write_text("existing")
            with patch.object(Path, "lstat", return_value=metadata(stat.S_IFDIR)), self.assertRaises(FileExistsError):
                exporter.export(closure, roots, output)
            self.assertEqual(marker.read_text(), "existing")
            self.assertEqual(list(output.iterdir()), [marker])


if __name__ == "__main__":
    unittest.main()
