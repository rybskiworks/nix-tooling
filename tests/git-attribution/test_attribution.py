"""Exercise the portable attribution CLI against isolated, real Git repositories."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ([os.environ["GIT_ATTRIBUTION_TEST_COMMAND"]]
           if os.environ.get("GIT_ATTRIBUTION_TEST_COMMAND")
           else [sys.executable, str(ROOT / "scripts/git-attribution.py")])
AUTHOR = "Pat Example <pat@example.org>"
OTHER = "Contributor <contributor@example.com>"
SHELL = os.environ.get("GIT_ATTRIBUTION_SHELL", "/bin/sh")


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="attribution-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT="0",
                        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
                        GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
        self.env["GIT_ATTRIBUTION_SHELL"] = SHELL
        self.git("init", "--initial-branch=main", "--template=")
        self.git("config", "user.name", "Fixture Author")
        self.git("config", "user.email", "fixture@example.com")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "attribution.coAuthor", AUTHOR)
        self.message = self.root / "message.txt"

    def run_process(self, command, *, success=True, input_text=None):
        result = subprocess.run(command, cwd=self.repo, env=self.env, input=input_text,
                                capture_output=True, text=True, check=False)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def git(self, *args, success=True):
        return self.run_process(["git", *args], success=success)

    def cli(self, *args, success=True, input_text=None):
        return self.run_process([*COMMAND, *args], success=success, input_text=input_text)

    def format(self, message, *args):
        self.message.write_text(message, encoding="utf-8")
        self.cli("format", "--file", str(self.message), *args)
        return self.message.read_text(encoding="utf-8")

    def stage(self, text="content"):
        (self.repo / "content.txt").write_text(text, encoding="utf-8")
        self.git("add", "content.txt")

    def editor(self, program):
        path = self.root / "editor.py"
        path.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport sys\np = Path(sys.argv[1])\n{program}\n", encoding="utf-8")
        path.chmod(0o755)
        self.env["GIT_EDITOR"] = str(path)

    def test_format_and_check_are_idempotent(self):
        formatted = self.format("feat: example\n\nA body.\n")
        self.assertEqual(formatted, f"feat: example\n\nA body.\n\nCo-authored-by: {AUTHOR}\n")
        self.cli("check", "--file", str(self.message))
        self.cli("format", "--file", str(self.message))
        self.assertEqual(self.message.read_text(), formatted)

    def test_preserves_foreign_trailers_and_existing_coauthors(self):
        formatted = self.format(f"feat: example\n\nReviewed-by: Reviewer <reviewer@example.com>\nCo-authored-by: {OTHER}\n")
        self.assertIn("Reviewed-by: Reviewer <reviewer@example.com>\n", formatted)
        self.assertIn(f"Co-authored-by: {OTHER}\n", formatted)
        self.assertIn(f"Co-authored-by: {AUTHOR}\n", formatted)

    def test_same_email_normalizes_duplicates_and_preserves_first_name(self):
        formatted = self.format(f"feat: example\n\nCo-authored-by: First Name <PAT@example.org>\nco-AUTHORED-by: {AUTHOR}\n")
        self.assertEqual(formatted.count("Co-authored-by:"), 1)
        self.assertIn("First Name <PAT@example.org>", formatted)
        self.cli("check", "--file", str(self.message))

    def test_check_rejects_duplicate_emails(self):
        self.message.write_text(f"feat: example\n\nCo-authored-by: {AUTHOR}\nCo-authored-by: Alias <PAT@example.org>\n")
        self.cli("check", "--file", str(self.message), success=False)

    def test_markdown_headings_dividers_and_examples_survive(self):
        body = f"# A proposal\n\nCo-authored-by: {OTHER}\n\n---\n\n# Validation\n\nPassed.\n"
        formatted = self.format(body)
        self.assertTrue(formatted.startswith(body))
        self.assertEqual(formatted.count(f"Co-authored-by: {OTHER}"), 1)
        self.assertTrue(formatted.endswith(f"Co-authored-by: {AUTHOR}\n"))
        self.cli("check", "--file", str(self.message))

    def test_crlf_is_preserved(self):
        self.message.write_bytes(b"feat: example\r\n\r\nBody.\r\n")
        self.cli("format", "--file", str(self.message))
        data = self.message.read_bytes()
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.cli("check", "--file", str(self.message))

    def test_explicit_policy_overrides_config_and_supports_multiple_authors(self):
        formatted = self.format("feat: example\n", "--co-author", OTHER, "--co-author", "Second <second@example.com>")
        self.assertNotIn(AUTHOR, formatted)
        self.assertIn(OTHER, formatted)
        self.assertIn("Second <second@example.com>", formatted)

    def test_strict_preserves_approved_humans_and_unrelated_trailers(self):
        self.git("config", "attribution.strict", "true")
        self.git("config", "attribution.allowedCoAuthor", OTHER)
        formatted = self.format(
            f"feat: example\n\nReviewed-by: Reviewer <reviewer@example.com>\n"
            f"Co-authored-by: {OTHER}\nCo-authored-by: Pat Example <PAT@example.org>\n"
        )
        self.assertIn("Reviewed-by: Reviewer <reviewer@example.com>\n", formatted)
        self.assertIn(f"Co-authored-by: {OTHER}\n", formatted)
        self.assertIn("Co-authored-by: Pat Example <PAT@example.org>\n", formatted)
        self.cli("check", "--file", str(self.message))
        self.assertNotIn(OTHER, self.format("feat: another change\n"))

    def test_strict_rejects_unapproved_footer_before_writes(self):
        self.git("config", "attribution.strict", "true")
        for value in (OTHER, "Unapproved Name <pat@example.org>"):
            with self.subTest(identity=value):
                original = f"feat: example\n\nCo-authored-by: {AUTHOR}\nCo-authored-by: {value}\n"
                self.message.write_text(original)
                for command in ("format", "check"):
                    result = self.cli(command, "--file", str(self.message), success=False)
                    self.assertIn("unapproved co-author identity", result.stderr)
                    self.assertEqual(self.message.read_text(), original)

    def test_strict_explicit_authors_cannot_expand_trusted_policy(self):
        self.git("config", "attribution.strict", "true")
        original = f"feat: example\n\nCo-authored-by: {AUTHOR}\n"
        for value in (OTHER, "Unapproved Name <PAT@example.org>"):
            with self.subTest(identity=value):
                self.message.write_text(original)
                for command in ("format", "check"):
                    result = self.cli(command, "--file", str(self.message),
                                      "--co-author", AUTHOR, "--co-author", value, success=False)
                    self.assertIn("unapproved co-author identity", result.stderr)
                    self.assertEqual(self.message.read_text(), original)

    def test_strict_explicit_authors_need_configured_approval(self):
        self.git("config", "attribution.strict", "true")
        self.git("config", "--unset-all", "attribution.coAuthor")
        self.message.write_text("feat: example\n")
        self.cli("format", "--file", str(self.message), "--co-author", AUTHOR, success=False)
        self.assertEqual(self.message.read_text(), "feat: example\n")

    def test_strict_range_rejects_unapproved_names_before_writes(self):
        self.stage()
        self.git("commit", "-m", "feat: base")
        base = self.git("rev-parse", "HEAD").stdout.strip()
        for value in (OTHER, "Unapproved Name <pat@example.org>"):
            with self.subTest(identity=value):
                self.stage(value)
                self.git("commit", "-m", f"feat: imported\n\nCo-authored-by: {value}")
                self.git("config", "attribution.strict", "true")
                original = f"feat: squash\n\nCo-authored-by: {AUTHOR}\n"
                self.message.write_text(original)
                result = self.cli("format", "--file", str(self.message),
                                  "--from-commits", f"{base}..HEAD", success=False)
                self.assertIn("unapproved co-author identity", result.stderr)
                self.assertEqual(self.message.read_text(), original)
                base = self.git("rev-parse", "HEAD").stdout.strip()

    def test_strict_allows_approved_imports_and_explicit_selection(self):
        self.git("config", "attribution.strict", "true")
        self.git("config", "attribution.allowedCoAuthor", OTHER)
        self.stage()
        self.git("commit", "-m", "feat: base")
        self.stage("second")
        self.git("commit", "-m", f"feat: imported\n\nCo-authored-by: {OTHER}")
        formatted = self.format("feat: squash\n", "--from-commits", "HEAD~1..HEAD")
        self.assertIn(OTHER, formatted)
        self.assertIn(AUTHOR, formatted)
        selected = self.format("feat: reviewed credit\n", "--co-author", OTHER)
        self.assertIn(OTHER, selected)
        self.assertNotIn(AUTHOR, selected)
        self.cli("check", "--file", str(self.message), "--co-author", OTHER)

    def test_strict_rejects_invalid_trusted_configuration_before_writes(self):
        self.git("config", "attribution.strict", "true")
        self.git("config", "attribution.allowedCoAuthor", "Invalid identity")
        self.message.write_text("feat: example\n")
        self.cli("format", "--file", str(self.message), success=False)
        self.assertEqual(self.message.read_text(), "feat: example\n")

    def test_strict_final_hook_rejects_editor_adding_unapproved_credit(self):
        self.git("config", "attribution.strict", "true")
        self.cli("install")
        self.stage()
        self.editor(f"p.write_text('feat: edited\\n\\nCo-authored-by: {AUTHOR}\\nCo-authored-by: {OTHER}\\n')")
        result = self.git("commit", "-m", "feat: initial", "--edit", success=False)
        self.assertIn("unapproved co-author identity", result.stderr)
        self.git("rev-parse", "--verify", "HEAD", success=False)

    def test_repeated_config_and_no_identity_inference(self):
        self.git("config", "--add", "attribution.coAuthor", OTHER)
        formatted = self.format("feat: example\n")
        self.assertIn(AUTHOR, formatted)
        self.assertIn(OTHER, formatted)
        self.assertNotIn("fixture@example.com", formatted)
        self.git("config", "--unset-all", "attribution.coAuthor")
        self.cli("format", "--file", str(self.message), success=False)

    def test_bad_explicit_identities_fail_before_writes(self):
        original = b"feat: example\n"
        for invalid in ["Missing Email", "Name <a@b>", "Name <a@@b.com>", "Name <a..b@example.com>",
                        "Name <a@example.com>\nCo-authored-by: Other <b@example.com>",
                        "Name\x01 <a@example.com>", "Name <a@example.com><b@example.com>",
                        "Name\u200b <a@example.com>"]:
            with self.subTest(invalid=repr(invalid)):
                self.message.write_bytes(original)
                self.cli("format", "--file", str(self.message), "--co-author", invalid, success=False)
                self.assertEqual(self.message.read_bytes(), original)

    def test_newline_in_config_is_not_interpreted_as_two_authors(self):
        self.git("config", "attribution.coAuthor", f"{AUTHOR}\n{OTHER}")
        self.message.write_text("feat: example\n")
        self.cli("format", "--file", str(self.message), success=False)
        self.assertEqual(self.message.read_text(), "feat: example\n")

    def test_malformed_existing_trailers_fail_before_writes(self):
        for trailer in ["Co-authored-by: invalid", "Co-authored-by: Name\n <a@example.com>"]:
            original = f"feat: example\n\n{trailer}\n"
            self.message.write_text(original)
            self.cli("format", "--file", str(self.message), success=False)
            self.assertEqual(self.message.read_text(), original)

    def test_stdin_and_stdout(self):
        formatted = self.cli("format", "--file", "-", input_text="feat: example\n").stdout
        self.assertIn(AUTHOR, formatted)
        self.cli("check", "--file", "-", input_text=formatted)

    def test_trailer_configuration_cannot_execute_commands_or_change_policy(self):
        marker = self.root / "unexpected-command"
        self.git("config", "trailer.co-authored-by.cmd", f"touch {marker}")
        self.git("config", "trailer.co-authored-by.key", "Changed-Key")
        formatted = self.format("feat: example\n")
        self.assertIn(f"Co-authored-by: {AUTHOR}", formatted)
        self.assertFalse(marker.exists())

    def test_commit_m_f_and_amend_preserve_native_identities(self):
        self.cli("install")
        self.stage()
        self.git("commit", "-m", "feat: first")
        self.assertIn(AUTHOR, self.git("log", "-1", "--format=%B").stdout)
        identities = self.git("log", "-1", "--format=%an <%ae>%n%cn <%ce>").stdout
        self.assertEqual(identities, "Fixture Author <fixture@example.com>\nFixture Author <fixture@example.com>\n")
        self.stage("second")
        self.message.write_text(f"feat: second\n\nCo-authored-by: {OTHER}\n")
        self.git("commit", "-F", str(self.message))
        self.git("commit", "--amend", "--no-edit")
        message = self.git("log", "-1", "--format=%B").stdout
        self.assertEqual(message.count(AUTHOR), 1)
        self.assertEqual(message.count(OTHER), 1)
        self.editor("pass")
        self.git("commit", "--amend", "--edit")
        message = self.git("log", "-1", "--format=%B").stdout
        self.assertEqual(message.count(AUTHOR), 1)
        self.assertEqual(message.count(OTHER), 1)
        self.message.write_text(message)
        self.cli("check", "--file", str(self.message), "--co-author", OTHER, "--co-author", AUTHOR)

    def test_empty_and_comment_only_prepare_cannot_manufacture_commits(self):
        for content in ["", "\n\n", "# Write a commit message.\n# Another comment.\n"]:
            self.message.write_text(content)
            self.cli("hook", "prepare-commit-msg", str(self.message))
            self.cli("hook", "commit-msg", str(self.message), success=False)
        self.cli("install")
        self.stage()
        self.git("commit", "-m", "", success=False)
        self.editor("pass")
        self.git("commit", success=False)
        self.git("rev-parse", "--verify", "HEAD", success=False)

    def test_final_hook_rejects_editor_removing_trailer(self):
        self.cli("install")
        self.stage()
        self.editor("p.write_text('feat: editor removed trailer\\n')")
        self.git("commit", "-m", "feat: initial", "--edit", success=False)
        self.git("rev-parse", "--verify", "HEAD", success=False)

    def test_interactive_first_message_seeds_attribution_for_editor(self):
        self.cli("install")
        self.stage()
        self.editor("p.write_text('feat: editor supplied message\\n\\n' + p.read_text())")
        self.git("commit")

    def test_final_hook_rejects_attribution_only_message(self):
        self.cli("install")
        self.stage()
        self.editor(f"p.write_text('Co-authored-by: {AUTHOR}\\n')")
        self.git("commit", "-m", "feat: initial", "--edit", success=False)

    def test_git_editor_comments_and_custom_comment_character(self):
        self.git("config", "core.commentChar", ";")
        self.cli("install")
        self.stage()
        self.editor("p.write_text('; added comment\\n' + p.read_text())")
        self.git("commit", "-m", "feat: example", "--edit")
        message = self.git("log", "-1", "--format=%B").stdout
        self.assertIn(AUTHOR, message)
        self.assertNotIn("added comment", message)

    def test_scissors_template_is_ignored_by_final_validator(self):
        self.git("config", "commit.cleanup", "scissors")
        self.message.write_text(f"feat: example\n\nCo-authored-by: {AUTHOR}\n\n# ------------------------ >8 ------------------------\nignored patch\n")
        self.cli("hook", "commit-msg", str(self.message))

    def test_scissors_preparation_preserves_discarded_section(self):
        self.git("config", "commit.cleanup", "scissors")
        tail = "# ------------------------ >8 ------------------------\nignored patch\n"
        self.message.write_text("feat: example\n\n" + tail)
        self.cli("hook", "prepare-commit-msg", str(self.message))
        self.assertTrue(self.message.read_text().endswith(tail))
        self.assertLess(self.message.read_text().index(AUTHOR), self.message.read_text().index(">8"))
        self.cli("hook", "commit-msg", str(self.message))

    def test_scissors_cleanup_truncates_only_when_editing(self):
        self.git("config", "commit.cleanup", "scissors")
        self.cli("install")
        self.stage()
        original = "feat: scissors\n\n# retained comment\n# ------------------------ >8 ------------------------\nretained noninteractive prose"
        self.git("commit", "-m", original)
        message = self.git("log", "-1", "--format=%B").stdout
        self.assertIn("retained noninteractive prose", message)
        self.assertIn("# retained comment", message)
        self.message.write_text(message)
        self.cli("check", "--file", str(self.message))
        self.stage("second")
        self.editor("pass")
        self.git("commit", "-m", original, "--edit")
        message = self.git("log", "-1", "--format=%B").stdout
        self.assertNotIn("retained noninteractive prose", message)
        self.assertNotIn(">8", message)
        self.assertIn("# retained comment", message)
        self.message.write_text(message)
        self.cli("check", "--file", str(self.message))
        self.stage("third")
        self.git("config", "--unset", "commit.cleanup")
        before = self.git("rev-parse", "HEAD").stdout
        result = self.git("commit", "-m", "feat: verbose", "--edit", "--verbose", success=False)
        self.assertIn("commit.cleanup=scissors", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, before)

    def test_preserved_comment_prose_in_noninteractive_commit(self):
        self.cli("install")
        self.stage()
        self.git("commit", "-m", "# subject preserved by -m")
        message = self.git("log", "-1", "--format=%B").stdout
        self.assertIn("# subject preserved by -m", message)
        self.message.write_text(message)
        self.cli("check", "--file", str(self.message))
        self.stage("second")
        self.git("commit", "-m", "feat: second\n\n# retained note", "--cleanup=verbatim")
        self.message.write_text(self.git("log", "-1", "--format=%B").stdout)
        self.cli("check", "--file", str(self.message))

    def test_final_hook_rejects_comments_after_footer(self):
        self.message.write_text(f"feat: example\n\nCo-authored-by: {AUTHOR}\n# preserved prose\n")
        self.cli("hook", "commit-msg", str(self.message), success=False)

    def test_fenced_examples_and_trailing_prose_are_not_trailers(self):
        for message in [f"feat: docs\n\n```text\nCo-authored-by: {AUTHOR}\n```\n",
                        f"feat: docs\n\n```text\n\nCo-authored-by: {AUTHOR}\n",
                        f"feat: docs\n\nCo-authored-by: {AUTHOR}\nThis is still prose.\n"]:
            with self.subTest(message=message):
                self.message.write_text(message)
                self.cli("check", "--file", str(self.message), success=False)
                # An unclosed Markdown fence cannot safely receive a footer.
                if "```text\n\n" in message:
                    self.cli("format", "--file", str(self.message), success=False)
                    self.assertEqual(self.message.read_text(), message)
                else:
                    self.cli("format", "--file", str(self.message))
                    self.assertTrue(self.message.read_text().startswith(message))
                    self.cli("check", "--file", str(self.message))

    def test_whitespace_only_lines_after_footer_are_valid(self):
        for ending in ("   \n", "\t\n"):
            self.message.write_text(f"feat: example\n\nCo-authored-by: {AUTHOR}\n{ending}")
            self.cli("check", "--file", str(self.message))
            self.cli("format", "--file", str(self.message))
            self.cli("check", "--file", str(self.message))

    def test_hook_chaining_reinstall_and_other_hooks_untouched(self):
        hooks = self.repo / ".git/hooks"
        hooks.mkdir(exist_ok=True)
        for name in ("prepare-commit-msg", "commit-msg", "pre-commit", "pre-push"):
            path = hooks / name
            path.write_text(f"#!{SHELL}\nprintf '{name}\\n' >> hook-order.txt\n")
            path.chmod(0o755)
        untouched = {name: (hooks / name).read_bytes() for name in ("pre-commit", "pre-push")}
        self.cli("install")
        self.cli("install")
        self.stage()
        self.git("commit", "-m", "feat: example")
        self.assertEqual((self.repo / "hook-order.txt").read_text(), "pre-commit\nprepare-commit-msg\ncommit-msg\n")
        for name, content in untouched.items():
            self.assertEqual((hooks / name).read_bytes(), content)

    def test_existing_hook_failures_are_preserved(self):
        for hook_name in ("prepare-commit-msg", "commit-msg"):
            with self.subTest(hook=hook_name):
                hooks = self.repo / ".git/hooks"
                hooks.mkdir(exist_ok=True)
                hook = hooks / hook_name
                hook.write_text(f"#!{SHELL}\nexit 37\n")
                hook.chmod(0o755)
                self.cli("install")
                self.message.write_text(f"feat: example\n\nCo-authored-by: {AUTHOR}\n")
                result = self.run_process([str(hook), str(self.message)], success=False)
                self.assertEqual(result.returncode, 37)
                hook.unlink()
                (hooks / f"{hook_name}.git-attribution-original").unlink()

    def test_existing_commit_msg_hook_mutation_is_checked_last(self):
        hooks = self.repo / ".git/hooks"
        hooks.mkdir(exist_ok=True)
        hook = hooks / "commit-msg"
        hook.write_text(f"#!{SHELL}\nprintf 'feat: replaced by existing hook\\n' > \"$1\"\n")
        hook.chmod(0o755)
        self.cli("install")
        self.stage()
        self.git("commit", "-m", "feat: example", success=False)

    def test_core_hookspath_is_refused_without_changes(self):
        shared = self.root / "shared-hooks"
        shared.mkdir()
        self.git("config", "core.hooksPath", str(shared))
        result = self.cli("install", success=False)
        self.assertIn("core.hooksPath", result.stderr)
        self.assertEqual(list(shared.iterdir()), [])
        self.assertEqual(self.git("config", "core.hooksPath").stdout.strip(), str(shared))

    def test_template_hook_upgrade_does_not_chain_stale_package(self):
        hooks = self.repo / ".git/hooks"
        hooks.mkdir(exist_ok=True)
        for name in ("prepare-commit-msg", "commit-msg"):
            hook = hooks / name
            hook.write_text("#!/stale/store/path/bin/sh\n# Managed by git-attribution.\nexit 91\n")
            hook.chmod(0o755)
        self.cli("install")
        self.stage()
        self.git("commit", "-m", "feat: template upgrade")
        for name in ("prepare-commit-msg", "commit-msg"):
            self.assertFalse((hooks / f"{name}.git-attribution-original").exists())

    def test_existing_backup_conflict_is_refused_before_installation(self):
        hooks = self.repo / ".git/hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "commit-msg").write_text("existing hook")
        (hooks / "commit-msg.git-attribution-original").write_text("existing backup")
        self.cli("install", success=False)
        self.assertFalse((hooks / "prepare-commit-msg").exists())
        self.assertEqual((hooks / "commit-msg").read_text(), "existing hook")

    def test_from_commits_preserves_all_existing_coauthors_not_native_authors(self):
        self.stage()
        self.git("commit", "-m", f"feat: base\n\nCo-authored-by: Base <base@example.com>")
        self.stage("second")
        self.git("commit", "-m", f"feat: one\n\nCo-authored-by: {OTHER}")
        self.stage("third")
        self.git("commit", "-m", "feat: two\n\nCo-authored-by: Last <last@example.com>")
        formatted = self.format("feat: squash\n", "--from-commits", "HEAD~2..HEAD")
        self.assertIn(OTHER, formatted)
        self.assertIn("Last <last@example.com>", formatted)
        self.assertIn(AUTHOR, formatted)
        self.assertNotIn("base@example.com", formatted)
        self.assertNotIn("fixture@example.com", formatted)
        self.cli("check", "--file", str(self.message))

    def test_bad_revision_range_fails_without_writing(self):
        self.message.write_text("feat: squash\n")
        self.cli("format", "--file", str(self.message), "--from-commits=--all", success=False)
        self.assertEqual(self.message.read_text(), "feat: squash\n")


if __name__ == "__main__":
    unittest.main()
