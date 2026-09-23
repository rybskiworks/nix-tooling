#!/usr/bin/env python3
"""Format and enforce explicit Git co-authorship without changing Git identities."""

import argparse
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import unicodedata


TRAILER = "Co-authored-by"
MANAGED_MARKER = "# Managed by git-attribution."
COAUTHOR_LINE = re.compile(
    r"^Co-authored-by[ \t]*:[ \t]*([^\n]*)(?:\n[ \t]+(?=[^\n]*[^ \t\n])[^\n]*)*",
    re.IGNORECASE | re.MULTILINE,
)
TRAILER_LINE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*[ \t]*:")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
EMAIL = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)


class AttributionError(Exception):
    """An invalid attribution policy, message, or repository configuration."""


def git(*args, input_text=None, cwd=None, env=None, missing_ok=False):
    result = subprocess.run(
        ["git", *args], input=input_text, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
        check=False,
    )
    if result.returncode and not (missing_ok and result.returncode == 1):
        raise AttributionError(result.stderr.strip() or "Git command failed")
    return result


def identity(value):
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise AttributionError("co-author identities must not contain control characters")
    match = re.fullmatch(r"([^<>]+?)\s+<([^<>]+)>", value.strip())
    if match is None:
        raise AttributionError("co-author identities must have the form 'Name <email@example.com>'")
    name, email = match.groups()
    local = email.split("@", 1)[0]
    if not EMAIL.fullmatch(email) or local.startswith(".") or local.endswith(".") or ".." in local:
        raise AttributionError("co-author identity contains an invalid email address")
    return f"{name.strip()} <{email}>", email.casefold()


def unique(identities):
    result = []
    seen = set()
    for value in identities:
        normalized, email = identity(value)
        if email not in seen:
            result.append(normalized)
            seen.add(email)
    return result


def configured_values(key):
    values = git("config", "--null", "--get-all", key, missing_ok=True).stdout.split("\0")
    return values[:-1] if values[-1] == "" else values


def configured_authors(explicit, approved):
    values = explicit or configured_values("attribution.coAuthor")
    admit_authors(values, approved)
    # Validate every entry before deduplication; malformed duplicates must not disappear.
    return unique(values)


def identity_key(value):
    normalized, email = identity(value)
    return normalized.rsplit(" <", 1)[0], email


def approved_authors():
    strict = git("config", "--type=bool", "--get", "attribution.strict", missing_ok=True).stdout.strip()
    if strict != "true":
        return None
    # Only configured identities grant admission. CLI arguments and imported
    # trailers request credits but cannot expand the trusted human policy.
    values = [*configured_values("attribution.coAuthor"),
              *configured_values("attribution.allowedCoAuthor")]
    return {identity_key(value) for value in values}


def admit_authors(values, approved):
    if approved is not None:
        for value in values:
            if identity_key(value) not in approved:
                raise AttributionError(
                    "unapproved co-author identity; review the human identities in "
                    "attribution.coAuthor and attribution.allowedCoAuthor"
                )


def footer_start(message):
    """Require a final trailer paragraph, excluding Markdown examples and prose."""
    lines = message.splitlines(keepends=True)
    while lines and not lines[-1].strip():
        lines.pop()
    start = len(lines)
    while start and lines[start - 1].strip():
        start -= 1
    block = lines[start:]
    if not block or not TRAILER_LINE.match(block[0]):
        return None
    if any(not TRAILER_LINE.match(line) and not line.startswith((" ", "\t")) for line in block):
        return None
    fence = None
    for line in lines[:start]:
        match = FENCE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not line[match.end():].strip():
                fence = None
    if fence is not None:
        return None
    return sum(len(line) for line in lines[:start])


class TrailerParser:
    """Use Git's trailer grammar with deterministic, command-free configuration."""

    def __init__(self, directory, comment="\x1f", approved=None):
        self.directory = directory
        self.comment = comment
        self.approved = approved
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CEILING_DIRECTORIES=str(Path(directory).parent))

    def interpret(self, message, *args):
        return git(
            "-c", f"core.commentString={self.comment}",
            "-c", f"core.commentChar={self.comment[0]}",
            "-c", "trailer.separators=:",
            "-c", "trailer.co-authored-by.key=Co-authored-by",
            "interpret-trailers", "--no-divider", *args,
            input_text=message, cwd=self.directory, env=self.env,
        ).stdout

    def authors_and_spans(self, message):
        start = footer_start(message)
        if start is None:
            return [], []
        values = []
        # The synthetic subject also allows validation of attribution-only messages;
        # the commit hook separately rejects those as empty commit descriptions.
        for line in self.interpret("message\n\n" + message[start:], "--parse").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().casefold() == TRAILER.casefold():
                values.append(value.strip())
        # Git reports parsed trailers, not source offsets. Match backwards so examples
        # earlier in a commit/PR body are never rewritten as if they were its footer.
        candidates = list(COAUTHOR_LINE.finditer(message, start))
        spans = []
        for value in reversed(values):
            while candidates:
                candidate = candidates.pop()
                raw = candidate.group(0).partition(":")[2].strip(" \t")
                unfolded = re.sub(r"\n[ \t]+", " ", raw).strip()
                if unfolded == value:
                    identity(raw)  # Reject multiline and malformed identities before writes.
                    end = candidate.end()
                    if message[end:end + 1] == "\n":
                        end += 1
                    spans.append((candidate.start(), end))
                    break
            else:
                raise AttributionError("could not locate a parsed co-author trailer in the message")
        admit_authors(values, self.approved)
        return values, spans

    def format(self, message, required):
        admit_authors(required, self.approved)
        existing, _ = self.authors_and_spans(message)
        existing_emails = {identity(value)[1] for value in existing}
        args = ["--where", "end", "--if-exists", "addIfDifferent", "--if-missing", "add"]
        for author in unique(required):
            if identity(author)[1] not in existing_emails:
                args.extend(["--trailer", f"{TRAILER}: {author}"])
        if footer_start(message) is None:
            footer = self.interpret("message\n", *args).partition("\n")[2].lstrip("\n")
            message = message.rstrip("\n") + "\n\n" + footer
        else:
            message = self.interpret(message, *args)
        values, spans = self.authors_and_spans(message)
        replacements = []
        seen = set()
        for value, (start, end) in zip(values, reversed(spans)):
            normalized, email = identity(value)
            replacement = "" if email in seen else f"{TRAILER}: {normalized}\n"
            replacements.append((start, end, replacement))
            seen.add(email)
        for start, end, replacement in reversed(replacements):
            message = message[:start] + replacement + message[end:]
        return message

    def check(self, message, required):
        admit_authors(required, self.approved)
        existing, _ = self.authors_and_spans(message)
        emails = [identity(value)[1] for value in existing]
        if len(set(emails)) != len(emails):
            raise AttributionError("duplicate co-author email; run git-attribution format")
        missing = [value for value in required if identity(value)[1] not in emails]
        if missing:
            raise AttributionError("missing required co-author trailer(s); run git-attribution format")


def comment_string():
    for key in ("core.commentString", "core.commentChar"):
        value = git("config", "--get", key, missing_ok=True).stdout.rstrip("\n")
        if value:
            if value == "auto":
                raise AttributionError("core.commentChar=auto is unsupported by hooks; configure an explicit comment character")
            return value
    return "#"


def commit_content(message, comment):
    lines = []
    for line in message.splitlines(keepends=True):
        if not line.startswith(comment):
            lines.append(line)
    return "".join(lines).strip()


def split_scissors(message, comment):
    offset = 0
    for line in message.splitlines(keepends=True):
        if line.rstrip() == f"{comment} ------------------------ >8 ------------------------":
            return message[:offset], message[offset:]
        offset += len(line)
    return message, ""


def split_comment_tail(message, comment):
    """Separate Git's editor notes so the final footer can follow those notes."""
    lines = message.splitlines(keepends=True)
    start = len(lines)
    found_comment = False
    while start:
        line = lines[start - 1]
        if line.startswith(comment):
            found_comment = True
        elif line.strip():
            break
        start -= 1
    if found_comment:
        return "".join(lines[:start]), "".join(lines[start:])
    return message, ""


def read_message(filename):
    data = sys.stdin.buffer.read() if filename == "-" else Path(filename).read_bytes()
    message = data.decode("utf-8")
    newline = "\r\n" if "\r\n" in message and "\n" not in message.replace("\r\n", "") else "\n"
    return message.replace("\r\n", "\n"), newline


def write_message(filename, message, newline):
    data = message.replace("\n", newline).encode("utf-8")
    if filename == "-":
        sys.stdout.buffer.write(data)
        return
    path = Path(filename)
    if path.read_bytes() == data:
        return
    # Git can supply a symlink as a message file; retain the link and update its target.
    path = path.resolve()
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            os.fchmod(handle.fileno(), mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def commit_authors(parser, revision_range):
    messages = git("log", "--format=%B%x00", "--no-show-signature", "--end-of-options", revision_range, "--").stdout
    authors = []
    for message in messages.split("\0"):
        values, _ = parser.authors_and_spans(message.lstrip("\n"))
        authors.extend(values)
    return unique(authors)


def hook_command():
    executable = os.environ.get("GIT_ATTRIBUTION_EXECUTABLE")
    if executable:
        if not Path(executable).is_absolute() or not os.access(executable, os.X_OK):
            raise AttributionError("GIT_ATTRIBUTION_EXECUTABLE must name an absolute executable path")
        return [executable]
    return [sys.executable, str(Path(__file__).resolve())]


def install():
    git("rev-parse", "--git-dir")
    if git("config", "--get", "core.hooksPath", missing_ok=True).returncode == 0:
        raise AttributionError(
            "core.hooksPath is configured; integrate 'git-attribution hook prepare-commit-msg \"$@\"' "
            "and 'git-attribution hook commit-msg \"$@\"' into that hook manager, or unset "
            "core.hooksPath before installing repository-local hooks"
        )
    directory = Path(git("rev-parse", "--git-path", "hooks").stdout.strip()).resolve()
    command = hook_command()
    shell = os.environ.get("GIT_ATTRIBUTION_SHELL", "/bin/sh")
    if not Path(shell).is_absolute() or not os.access(shell, os.X_OK) or any(char.isspace() for char in shell):
        raise AttributionError("GIT_ATTRIBUTION_SHELL must name an absolute executable shell without whitespace")
    changes = []
    for name in ("prepare-commit-msg", "commit-msg"):
        hook = directory / name
        backup = directory / f"{name}.git-attribution-original"
        lines = hook.read_bytes().splitlines() if hook.is_file() else []
        managed = len(lines) >= 2 and lines[0].startswith(b"#!") and lines[1] == MANAGED_MARKER.encode()
        exists = hook.exists() or hook.is_symlink()
        if exists and not managed and (backup.exists() or backup.is_symlink()):
            raise AttributionError(f"refusing to overwrite existing hook backup: {backup}")
        script = f"#!{shell}\n{MANAGED_MARKER}\n"
        script += f"if [ -x {shlex.quote(str(backup))} ]; then\n"
        script += f"  {shlex.quote(str(backup))} \"$@\" || exit \"$?\"\nfi\n"
        script += f"exec {shlex.join([*command, 'hook', name])} \"$@\"\n"
        changes.append((hook, backup, exists and not managed, script))
    directory.mkdir(parents=True, exist_ok=True)
    for hook, backup, preserve, script in changes:
        if preserve:
            hook.rename(backup)
        # Replace a managed symlink rather than writing through it to shared state.
        if hook.is_symlink():
            hook.unlink()
        hook.write_text(script, encoding="utf-8")
        hook.chmod(0o755)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("format", "check"):
        command = subcommands.add_parser(name)
        command.add_argument("--file", required=True, help="message file, or - for stdin/stdout")
        command.add_argument("--co-author", action="append", default=[], help="explicit Name <email> (repeatable)")
        if name == "format":
            command.add_argument("--from-commits", help="include existing co-author trailers from a Git revision range")
    subcommands.add_parser("install", help="install repository-local hooks, retaining existing hooks")
    hook = subcommands.add_parser("hook")
    hook.add_argument("name", choices=("prepare-commit-msg", "commit-msg"))
    hook.add_argument("file")
    hook.add_argument("hook_args", nargs="*")
    return parser.parse_args()


def main():
    args = arguments()
    if args.command == "install":
        install()
        return
    message, newline = read_message(args.file)
    is_hook = args.command == "hook"
    comment = comment_string() if is_hook else "\x1f"
    cleanup = git("config", "--get", "commit.cleanup", missing_ok=True).stdout.strip() if is_hook else ""
    editing = os.environ.get("GIT_EDITOR") != ":"
    strips_comments = is_hook and (cleanup == "strip" or (cleanup in ("", "default") and editing))
    # Git's scissors mode otherwise behaves like whitespace: ordinary comments
    # survive, and the scissors section is discarded only when editing.
    truncates_scissors = is_hook and cleanup == "scissors" and editing
    content, discarded = split_scissors(message, comment) if truncates_scissors else (message, "")
    if strips_comments and editing and split_scissors(message, comment)[1]:
        raise AttributionError("an edited message contains scissors; configure commit.cleanup=scissors so hooks can identify the final message")
    approved = approved_authors()
    required = configured_authors(getattr(args, "co_author", []), approved)
    with tempfile.TemporaryDirectory(prefix="git-attribution-") as directory:
        parser = TrailerParser(directory, approved=approved)
        revision_range = getattr(args, "from_commits", None)
        if revision_range:
            required = unique([*required, *commit_authors(parser, revision_range)])
        if not required:
            raise AttributionError("configure attribution.coAuthor or supply --co-author 'Name <email@example.com>'")
        if args.command == "check" or (is_hook and args.name == "commit-msg"):
            parser.check(content, required)
            if is_hook:
                _, spans = parser.authors_and_spans(content)
                for start, end in sorted(spans, reverse=True):
                    content = content[:start] + content[end:]
                if strips_comments:
                    content = commit_content(content, comment)
                if not content.strip():
                    raise AttributionError("commit message needs a subject/body in addition to co-author trailers")
        else:
            body, comments = split_comment_tail(content, comment) if strips_comments else (content, "")
            formatted = parser.format(body, required)
            if comments:
                start = footer_start(formatted)
                if start is None:
                    raise AttributionError("could not locate formatted trailer footer")
                formatted = formatted[:start] + comments.rstrip("\n") + "\n\n" + formatted[start:]
            parser.check(formatted, required)
            write_message(args.file, formatted + discarded, newline)


if __name__ == "__main__":
    try:
        main()
    except (AttributionError, OSError, UnicodeError) as error:
        print(f"git-attribution: {error}", file=sys.stderr)
        sys.exit(1)
