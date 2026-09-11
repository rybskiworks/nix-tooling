# Contributing

Target `main` with a focused PR and conventional subject. Explain which public
module, package, helper or consumer is affected, and distinguish compatible fixes
from changes to defaults, platforms or runtime behavior.

Run the documented [CI commands](docs/ci-releases.md). Offline Python contracts,
ordinary-check evaluation/building, whole-flake shell validation and optional
native/KVM checks prove different things. Record commands, results, exact revisions
and anything not run. Use `./scripts/devenv-shell.sh` for the interactive environment;
never turn shell entry into an incidental state-migration hook.

This repository owns shared pins. Consumers follow them; supplier inputs do not
follow consumers back. Keep immutable revisions and reviewed NAR hashes. Generate
lockfiles with Nix, never invent integrity hashes or commit local paths. Promote
changes to consumers through separate tested PRs.

Do not initialize trackers, migrate databases, activate daemons, deploy images or
rewrite a consumer's NixOS stateVersion as incidental development work. Preserve
protocol fixtures, source patches and negative controls. Ignore generated artifacts,
not meaningful source. Read [security reporting](SECURITY.md) before disclosing a
vulnerability or copying operator data into a test.

Changing version.txt or release-note categories does not publish a release. Public
compatibility, downstream evidence, provenance and publisher authority must be
established separately. See [governance](docs/github-governance.md). The outstanding
project-license decision belongs to the owner, not an automated cleanup PR.
