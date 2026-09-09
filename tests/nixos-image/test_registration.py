"""Exercise the actual registration shell with an inert Nix command double."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.metadata = self.root / "metadata"
        self.store = self.root / "store"
        self.state = self.root / "state"
        self.bin = self.root / "bin"
        for path in (self.metadata, self.store, self.bin):
            path.mkdir()
        self.log = self.root / "calls"
        self.script = Path(os.environ.get("REGISTER_SCRIPT", Path(__file__).parents[2] / "lib/guest/register-store.sh"))
        fake = self.bin / "nix-store"
        fake.write_text("#!" + shutil.which("bash") + "\n"
                        'printf "%s|%s\\n" "$NIX_REMOTE" "$*" >> "$CALL_LOG"\n'
                        'if [[ "$1" == --load-db ]]; then cat >/dev/null; fi\n'
                        'if [[ "$1" == --query ]]; then printf "%s\\n" "${@:3}"; '
                        'if [[ -n "${MISSING_DEPENDENCY:-}" ]]; then printf "%s\\n" "$MISSING_DEPENDENCY"; fi; fi\n'
                        'if [[ "$1" == "${FAIL_COMMAND:-}" ]]; then exit 23; fi\n')
        fake.chmod(0o700)

    def manifest(self, name, suffix="payload"):
        root = self.store / ("0" * 32 + "-" + suffix)
        root.mkdir(exist_ok=True)
        (self.metadata / f"{name}.roots").write_text(str(root) + "\n")
        (self.metadata / f"{name}.registration").write_text("synthetic registration\n")
        return root

    def run_script(self, fail="", missing=""):
        env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "CALL_LOG": str(self.log), "FAIL_COMMAND": fail, "NIX_REMOTE": "daemon",
               "MISSING_DEPENDENCY": missing}
        return subprocess.run([shutil.which("bash"), "-c",
                               'source "$1"; register_images "$2" "$3" "$4"',
                               "registration-test", str(self.script), str(self.metadata),
                               str(self.state), str(self.store)], env=env, capture_output=True, timeout=5)

    def test_base_leaf_and_reapply_are_additive(self):
        base = self.manifest("base")
        leaf = self.manifest("leaf", "leaf")
        for _ in range(2):
            self.assertEqual(self.run_script().returncode, 0)
        for name, root in (("base", base), ("leaf", leaf)):
            self.assertEqual((self.state / "gcroots/guest-images" / name / "0").readlink(), root)
        calls = self.log.read_text().splitlines()
        self.assertEqual(sum(line == "local|--load-db" for line in calls), 4)
        self.assertTrue(all(line.startswith("local|") for line in calls))

    def test_missing_base_is_rejected_before_database_use(self):
        self.manifest("leaf")
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.log.exists())

    def test_missing_pair_and_symlink_are_rejected(self):
        self.manifest("base")
        registration = self.metadata / "base.registration"
        registration.unlink()
        self.assertNotEqual(self.run_script().returncode, 0)
        registration.symlink_to(self.metadata / "base.roots")
        self.assertNotEqual(self.run_script().returncode, 0)

    def test_root_escape_and_missing_path_are_rejected(self):
        self.manifest("base")
        for value in ("/etc/passwd", str(self.store / "../escape"), str(self.store / ("0" * 32 + "-absent")), ""):
            (self.metadata / "base.roots").write_text(value + "\n")
            self.assertNotEqual(self.run_script().returncode, 0)

    def test_load_or_validity_failure_cannot_create_gc_roots(self):
        self.manifest("base")
        for command in ("--load-db", "--check-validity", "--query"):
            self.assertNotEqual(self.run_script(command).returncode, 0)
            self.assertFalse((self.state / "gcroots/guest-images/base").exists())

    def test_missing_transitive_member_cannot_create_gc_roots(self):
        self.manifest("base")
        self.assertNotEqual(self.run_script(missing=str(self.store / ("0" * 32 + "-missing"))).returncode, 0)
        self.assertFalse((self.state / "gcroots/guest-images/base").exists())

    def test_unpaired_registration_is_not_silently_ignored(self):
        self.manifest("base")
        (self.metadata / "lost.registration").write_text("synthetic\n")
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.log.exists())


class LeafLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = Path(os.environ.get("LEAF_CHECK_SCRIPT", Path(__file__).parents[2] / "lib/guest/check-leaf.sh"))

    def check(self):
        return subprocess.run([shutil.which("bash"), "-c", 'source "$1"; check_leaf_root "$2"',
                               "leaf-test", str(self.script), str(self.root)],
                              capture_output=True, timeout=5)

    def test_unrelated_leaf_configs_and_real_ancestors_are_allowed(self):
        (self.root / "etc/ssl/certs").mkdir(parents=True)
        (self.root / "etc/service.conf").write_text("synthetic\n")
        self.assertEqual(self.check().returncode, 0)

    def test_exact_protected_file_is_rejected(self):
        (self.root / "etc").mkdir()
        (self.root / "etc/hosts").write_text("replacement\n")
        self.assertNotEqual(self.check().returncode, 0)

    def test_file_ancestors_are_rejected(self):
        for ancestor in ("etc", "etc/ssl", "etc/ssl/certs", "nix"):
            with self.subTest(ancestor=ancestor):
                path = self.root / ancestor
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("replacement\n")
                self.assertNotEqual(self.check().returncode, 0)
                path.unlink()

    def test_dangling_and_sibling_directory_ancestor_links_are_rejected(self):
        sibling = self.root / "sibling"
        sibling.mkdir()
        for ancestor in ("etc", "etc/ssl", "etc/ssl/certs", "nix"):
            for target in (self.root / "absent", sibling):
                with self.subTest(ancestor=ancestor, target=target.name):
                    path = self.root / ancestor
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.symlink_to(target)
                    self.assertNotEqual(self.check().returncode, 0)
                    path.unlink()


if __name__ == "__main__":
    unittest.main()
