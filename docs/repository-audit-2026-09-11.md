# Repository audit: 2026-09-11

Baseline: `1120aa22cddf4a9a3424f38aadbebadd8a963c4b`.
Scope: supplier pin ownership, repository hygiene, documentation, CI contracts and
observable governance. This is not a new boot, package or protocol validation.

## Findings and disposition

| Priority | Finding | Disposition |
| :--- | :--- | :--- |
| High | CI called bare whole-flake checks while local documentation required an explicit devenv-root input. | Enumerate/evaluate ordinary check derivations without evaluating the interactive shell; full tier builds exactly that set. |
| High | Full checks were manual/label-only, not selected on main pushes. | Compile ordinary checks for source PRs, unknown diffs, main pushes, merge groups and manual runs. Only known narrative-only PRs may skip. |
| High | Readable repository/inherited ruleset enumeration returned empty. | Disabled proposals and staged host-side verification; no administration settings changed. |
| Medium | README used floating/sibling-input examples despite reproducibility requirements. | Immutable already-landed example, one-way follows and explicit local-override boundaries. |
| Medium | Hook documentation conflated git-hooks.nix's installer with devenv's shell-entry task. | Distinguish the disabled installation script from the retained hook-shim installation task; shell entry skips lint/format tasks, not all hook mutations. |
| Medium | No project license file was present in the inspected root tree. | Flag an owner decision; do not copy Workestrate's license or add a false badge. |
| Medium | Workflow security/hygiene coverage differed from Workestrate. | Pinned zizmor, immutable-reference/hygiene checks, Fenix lock validation and regression tests. |
| Low | Python test caches were not ignored; README was a long implementation narrative. | Ignore generated Python caches and organize the landing page around consumers/modules/verification. |

## Preserved inputs and meaningful source

The supplier already owns Fenix and nixpkgs; it does not need a self-referential
tooling input. Dependency revisions and flake.lock are unchanged. This PR does not
replace the default Dolt binary package, initialize Beads, choose a host daemon,
mutate images/stateVersion or invent a release.

Fenix remains the compiler authority. The offline supplier validator checks an
immutable Fenix lock, while the consumer resolver derives the exact Rust version
from that locked manifest. No second compiler-version file is introduced.

No tracked supplier build/database artifacts were identified that justified
removing source or tests. Guest fixtures, source patches and protocol controls
remain intact. Cleanup is not permission to delete meaningful content for a larger
diff, rewrite history or migrate operator state.

## CI scope and evidence

CI / required remains the stable aggregate. Evaluation, compiled ordinary checks
and optional native/KVM/protocol tests remain distinct. The runner rejects empty or
malformed output, forbids lock updates and import-from-derivation, validates check
names/derivation paths, and uses argument arrays instead of evaluated shell input.

It deliberately does not claim whole-flake/development-shell validation, which
still requires the documented root input. Optional NixOS VM tests and Microsandbox
guest activation are not silently included. See [CI contracts](ci-releases.md).

The offline Python suite passed 27 tests during preparation: selection, invalid
metadata, gate failures, wrong/mutable suppliers, follows cycles, manifest parsing,
network failures, artifact classification and Nix subprocess negative controls.
Mocked subprocess tests are not real Nix evaluation/build evidence.

Nix, Cargo, actionlint, zizmor and KVM were unavailable in the execution environment;
container cloning was also unavailable. The GitHub connection provided reads/writes
and Python tests used locally reconstructed files. Hosted checks must be reviewed
before merge. The initial main check-run listing showed Dependabot only, not a
passing supplier CI pipeline.

## Next decisions

Activate verified protections and independent review; restrict agent workflow
execution separately from branch naming. Choose the project license. Test an exact
supplier/consumer compatibility matrix covering Workestrate packaging/bootstrap,
the runtime compiler path and optional guest profiles before promoting a revision.
Version public exports deliberately before introducing provenance-bearing release
automation. Keep release/signing authority separate from consumer/PR builds.

See [governance](github-governance.md) for actor/event execution protections, rollout,
negative permission tests and policy prerequisites.

## Evidence

- [Original CI](https://github.com/rybskiworks/nix-tooling/blob/1120aa22cddf4a9a3424f38aadbebadd8a963c4b/.github/workflows/ci.yml)
- [Shared workflow](https://github.com/rybskiworks/.github/blob/3a7ef7dd7d23aedba866cb3a3a5df26ff2f86a7c/.github/workflows/reusable-nix-check.yml)
- [Original setup documentation](https://github.com/rybskiworks/nix-tooling/blob/1120aa22cddf4a9a3424f38aadbebadd8a963c4b/README.md)
- [Flake ownership/outputs](https://github.com/rybskiworks/nix-tooling/blob/1120aa22cddf4a9a3424f38aadbebadd8a963c4b/flake.nix)
- [Base shell module](https://github.com/rybskiworks/nix-tooling/blob/1120aa22cddf4a9a3424f38aadbebadd8a963c4b/devenvModules/base.nix)
