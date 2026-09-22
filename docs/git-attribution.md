# Shared Git co-author attribution

`packages.x86_64-linux.git-attribution` provides one formatter and validator for
local commit messages, pull request descriptions and final squash bodies. Its
Python standard-library implementation also runs without Nix. It has no network
client, credential input or signing operation.

## Attribution contract

GitHub recognizes a final trailer block separated from the description by a blank
line. Each co-author occupies one line:

```text
feat: explain the change

Explain the resulting behavior and why it matters.

Co-authored-by: Pat Example <pat@example.org>
```

The email must be associated with the intended GitHub account. The name need not
be a GitHub username. Use the person's approved public or GitHub no-reply address.
The name and email are public metadata, including when configured through Nix.

Git's primary author, committer, authentication account, co-author trailers and
signature are separate. Authenticating with a machine account does not set the
Git author. A person already named as author does not become a second contributor
when the same email appears in a trailer. This package never changes author,
committer, credentials or signing configuration, and never invents agent credits.

## Package and configuration

After promoting a reviewed supplier revision, consumers can add
`inputs.tooling.packages.${system}.git-attribution` to their development or image
packages. The runtime dependencies are packaged with the executable; consumers
do not need to reproduce Python or Git dependency selection.

Configure the actual collaborators in Git config, independently of the agent:

```ini
[attribution]
    coAuthor = Pat Example <pat@example.org>
    coAuthor = Sam Example <sam@example.org>
```

Put this in the appropriate operator-managed global configuration or a shared
include. Do not replace a workload's credential helper, author or signing settings
to configure attribution. Repeated `attribution.coAuthor` entries are additive
across system, global and local Git configuration; a local entry does not erase
global collaborators. Use conditional includes or separate workload profiles
when repositories need different collaborators. No identity is inferred from a
directory name or authenticated account.

For NixOS guests, one opt-in module supplies the package, `/etc/gitconfig` entries
and a clone/init template:

```nix
{
  imports = [ inputs.tooling.nixosModules.gitAttribution ];
  programs.gitAttribution = {
    enable = true;
    coAuthors = [ "Pat Example <pat@example.org>" ];
  };
}
```

Import this into the shared agent guest profile so Codex, Prime, Pi and other
clients receive the same policy. Non-NixOS images can include the package, use the
same Git configuration, and select the package's
`share/git-attribution/template` as `init.templateDir`. Image layers that retain
a prebuilt base can use that package/configuration path without reevaluating or
replacing the base system.

`installTemplate = false` leaves template ownership with the consumer. Compose
the package's two hook entrypoints into the existing template in that case.
`GIT_TEMPLATE_DIR`, `git init --template`, higher-precedence Git configuration and
`core.hooksPath` can override template behavior. The module does not claim to
enforce policy against those overrides.

## Commit hooks and existing checkouts

The template applies to new clones and repositories. For an existing checkout:

```sh
git-attribution install
```

Installation is explicit. It preserves existing executable `prepare-commit-msg`
and `commit-msg` hooks, running each before its attribution step and preserving
failure. Reinstallation is idempotent; `pre-commit` and `pre-push` remain owned by
their current installer. The installer refuses configured `core.hooksPath`, which
may refer to a shared or immutable directory. Integrate through that directory's
owner instead of replacing it or silently ignoring repository hooks:

```sh
git-attribution hook prepare-commit-msg "$@"
git-attribution hook commit-msg "$@"
```

Use each command in its corresponding hook, after the existing hook logic.
`prepare-commit-msg` adds the trailer before editing; `commit-msg` validates the
final edited message. Preparation can seed the footer into an empty editor
buffer; final validation rejects messages containing only attribution or
discarded comments, so this cannot manufacture a successful blank commit.
The hooks account for Git comments and scissors; ordinary file formatting
preserves Markdown headings and separators. Keep the trailer block last even
when editing comment lines: final validation deliberately rejects mixed
prose/trailer blocks and trailers shown only inside fenced Markdown examples.

Git does not expose per-command `--cleanup` overrides to `commit-msg`. Use the
normal editor/non-editor defaults or configure `commit.cleanup` explicitly.
An override that differs from the configured behavior may require formatting
the final message file explicitly; the hook conservatively rejects ambiguous
footer placement instead of assuming a later cleanup will repair it.
Configure an explicit `core.commentChar` or `core.commentString` when changing
the default comment prefix; `core.commentChar = auto` is rejected by the hooks.
For edited messages containing a scissors marker, including verbose commit
templates, configure `commit.cleanup = scissors` explicitly. Otherwise the
hooks cannot reliably distinguish a literal marker from Git's verbose diff
boundary and refuse the ambiguous message.

The installer records an absolute executable path. Keep a Nix profile or image
reference to its package so garbage collection cannot invalidate that path, and
reinstall existing checkout hooks when promoting a package revision. Never fix
a missing executable by disabling validation.

## PR descriptions and squash commits

Use the same formatter for a PR description written to a file:

```sh
git-attribution format --file /tmp/pr-body.md
git-attribution check --file /tmp/pr-body.md
gh pr create --base main --title 'feat: explain the change' --body-file /tmp/pr-body.md
```

Explicit `--co-author 'Name <email>'` arguments can be repeated; when supplied,
they replace the configured required list for that invocation. Formatting
preserves existing co-authors and unrelated trailers, and deduplicates identities
by email. A trailer mentioned in prose is not a substitute for the final block.

GitHub squash merges create a new commit on the server, where local hooks do not
run. Configure repository defaults to `squash_merge_commit_title = PR_TITLE` and
`squash_merge_commit_message = PR_BODY` through an authorized administrator. These
are editable defaults, not enforcement, and this package does not change them.

The host merge workflow should prepare the exact body it will submit. Starting
with the reviewed PR description, preserve existing co-author trailers from the
reviewed commit range as well:

```sh
git-attribution format --file /tmp/squash-body.md --from-commits BASE_SHA..HEAD_SHA
git-attribution check --file /tmp/squash-body.md
gh pr merge 123 --squash --subject 'feat: explain the change' \
  --body-file /tmp/squash-body.md --match-head-commit HEAD_SHA
```

Replace the example number and SHA placeholders with reviewed values from the
target PR. The range contributes only existing co-author trailers; it does not
infer contributors from native author fields. Review those authors as well when
combining independently authored work. A head change requires fresh validation.
Web merges require the same check of the final message in the merge dialog.
Verify the resulting merge commit's message and signature after publication.

For CI, read the PR description from the event JSON into a file and call `check`
with an explicit policy list from trusted configuration. Do not interpolate PR
text into a shell script. Include PR `edited` events so removing a footer reruns
the check. Keep exceptions for dependency automation or unrelated contributors
explicit instead of falsely attributing every repository change to one person.
CI validation and branch protection must be configured separately; local hooks
are bypassable and a successful local check is not observed GitHub enforcement.

## Adoption and verification

Promote the supplier first. Then update and test the immutable supplier pin in
each workload/fleet repository. Do not publish consumer pins pointing to an
unmerged supplier branch or a local checkout. Local input overrides are useful
for tests, and must not be saved into a published lockfile.

For each agent, check the actual image package list, runtime Git config and
checkout hook path. Agent names alone do not prove inheritance. Remove conflicting
legacy machine-attribution logic through its owning template, preserve credential
helpers, configure the requested human co-authors, and migrate persistent
checkout hooks explicitly. Shared prompts should direct every agent to use the
same helper for PR descriptions and final squash bodies. This closes the gap
between local Git hooks and server-side merge messages without a per-agent
formatter implementation.

The package ships `share/git-attribution/agent-instructions.md` for composition
into each workload's existing instruction mechanism. Codex, Prime and Pi can
consume the same text; their workload owners choose the appropriate instructions
file. The package never rewrites a checkout's `AGENTS.md` or client settings.

Run the portable Git integration suite and the packaged/module check:

```sh
python3 tests/git-attribution/test_attribution.py
nix build --no-link --no-update-lock-file .#checks.x86_64-linux.git-attribution
```

The suite uses disposable Git repositories and synthetic identities. The Nix
check runs the same suite against the packaged executable, reads the emitted
NixOS configuration with Git, and makes a commit in a clone using the generated
template. Configuration evaluation and local Git behavior do not demonstrate
running workload adoption or GitHub squash behavior. Verify those after the
corresponding deployment and host-side publication steps.

Co-author metadata does not supply a signature or confer permission to publish.
Where verified signed commits are required, use the configured host signing
workflow. Keep private keys and authentication tokens out of Nix inputs, test
fixtures, commit messages and reports.

## Sources

- [GitHub co-authored commits](https://docs.github.com/en/pull-requests/how-tos/commit-changes/creating-a-commit-with-multiple-authors)
- [Git trailer parsing and insertion](https://git-scm.com/docs/git-interpret-trailers)
- [Git hooks and bypass behavior](https://git-scm.com/docs/githooks)
- [GitHub squash message defaults](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/configuring-commit-squashing-for-pull-requests)
- [GitHub CLI explicit merge body and head guard](https://cli.github.com/manual/gh_pr_merge)
- [GitHub signature verification](https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification)
