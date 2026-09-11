# CI, tooling versions and downstream promotion

`main` is the integration branch. version.txt is source-contract metadata, not proof
that a release exists. Dependency pins, packages and runtime state are unchanged.

## CI tiers

Every main-targeting PR runs offline policy/metadata tests, supplier-lock checks,
actionlint, zizmor and ordinary-check evaluation. Source-changing PRs and
unknown/empty diffs select compiled ordinary checks; only known narrative-only
PRs may skip compilation. Main pushes, merge groups, manual dispatch and the nix-ci
label also select it. Hosted builds are bounded to two build jobs/two cores and a
120-minute compiled-check timeout.

`CI / required` retains a tested explicit dependency contract: policy/evaluation
must succeed and compilation may skip only if explicitly not selected. Failed,
cancelled, missing and unexpectedly skipped jobs fail closed. Merge-group support
does not enable a merge queue.

```sh
python3 -m unittest discover -s scripts/ci -p 'test_*.py' -v
python3 scripts/ci/contract.py metadata
python3 scripts/ci/toolchain.py check --role supplier
python3 scripts/ci/check_repository.py
python3 scripts/ci/run_checks.py evaluate
python3 scripts/ci/run_checks.py full
```

The runner enumerates checks.x86_64-linux and evaluates derivation paths, then
builds exactly that set for full. It rejects empty/malformed output, disables
import-from-derivation, forbids lock updates and checks lockfile cleanliness.
Subprocess argument arrays avoid executable shell interpolation from check names.

**This is not whole-flake validation.** It does not evaluate the interactive shell
or optional package/VM outputs. Evaluation/fetching still have costs and are not
promised network-free. Optional native/KVM/image activation tests remain separate.

The former shared organization workflow invoked bare whole-flake checks, conflicting
with this repository's documented devenv-root requirement. Selecting ordinary checks
repairs this repository without changing the contract for every other shared-workflow
consumer. A future reusable interface should accept a reviewed check selection, not
arbitrary executable commands.

## Separate whole-flake/development-shell validation

Use `./scripts/devenv-shell.sh` for interactive work. For whole-flake validation in
a disposable checkout, provide the explicit root:

```sh
root_file="$(mktemp)"
trap 'rm -f "$root_file"' EXIT
printf '%s' "$PWD" > "$root_file"
nix flake check --no-write-lock-file \
  --option allow-import-from-derivation false --max-jobs 1 --cores 2 \
  --override-input devenv-root "file+file://$root_file"
git diff --exit-code HEAD -- flake.lock
```

The override changes an evaluation input without writing it to the lock. Do not
claim an unchanged input graph or commit that temporary path. Strict locked CI does
not need the override. Hook installation, tracker initialization, daemon migration
and guest activation remain independent operations.

## SemVer and compatibility

Version documented public attributes, NixOS/devenv options/defaults, helper semantics,
platform/toolchain behavior and observable image contracts. Do not classify every
pin bump as a patch. In 0.x, breaking public changes require a documented minor bump
and migration notes; 1.0 needs an explicit compatibility commitment. Never bump a
consumer's NixOS stateVersion as a routine tooling upgrade.

A root version.txt release-PR strategy is a candidate, not an enabled publisher.
Inventory tags/releases, define the initial baseline and complete-repository change
accounting, and validate changelog consistency before adding a release calculator.
Generated release-note categories do not advance source versions.

## Promotion to Workestrate

Workestrate is public at this audit baseline and its integration target is now main
following PR #32. Keep consumers on a reviewed immutable tooling revision until
supplier and downstream evidence supports promotion. Advance the intended input/lock
subgraph in a separate PR; resolve releases to exact commits, not mutable refs.

A compatibility coordinator should test exact supplier/consumer revisions and report
sanitized results. Public consumer tests do not need a private-repository token.
Any future private tests belong in a suitably private/trusted context, never with
broad credentials exposed to public PR code. Missing required downstream evidence
blocks promotion even when the supplier's own ordinary checks pass.

## Publication and administration

Keep build/test jobs secretless. A separate narrow publisher verifies the approved
source SHA and same-SHA evidence, attaches complete checksummed assets, SBOM/provenance
and input revisions to a draft, and publishes after verification. Enable immutable
release/tag controls deliberately; editable release text is not a provenance artifact.

The agent draft-PR caller remains opt-in and never approves/merges. This change adds
no publishing key, private coordinator, cache deployment, native KVM worker or auto-merge.
Use [governance](github-governance.md) for staged host-side settings and review prerequisites.

The same small bootstrap verifier is present in both repositories so neither executes
mutable remote code or consumes the companion unmerged PR. It owns no compiler-version
pin. After tested promotion, a versioned supplier verification interface can remove
the copy; do not fetch scripts from main just to deduplicate them.
