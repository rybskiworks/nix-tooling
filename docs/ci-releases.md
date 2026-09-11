# CI, tooling versions and downstream promotion

This foundation leaves all current flake inputs, owned toolchain pins, modules, packages and existing tests unchanged. `version.txt` introduces an unpublished source-contract version, not a declaration that a Git tag or GitHub Release already exists.

## CI tiers

Every PR runs metadata tests and workflow lint, plus pinned Nix evaluation through the reviewed reusable organization workflow. The normal evaluation tier uses `nix flake check --no-build --no-update-lock-file`: it is not a claim that packages or guest runtimes were built and tested.

Adding `nix-ci`, or manually dispatching the workflow, selects full flake checks. Selected full checks participate in `CI / required`; failed, cancelled or unexpectedly skipped checks fail closed. Expensive checks are not made mandatory on every PR before hosted-runner resource requirements and caching have been established. Only add an administrative required-check rule after real runs establish the exact check context and emitting App.

The shared implementation is pinned to `3a7ef7dd7d23aedba866cb3a3a5df26ff2f86a7c` in the organization foundation PR. Review that code before adopting it. No multi-repository token, publisher credential or private consumer checkout occurs in this public CI. Ordinary evaluation may still realize import-from-derivation dependencies; do not describe it as universally zero-build or free.

```sh
python3 -m unittest discover -s scripts/ci -p 'test_*.py' -v
python3 scripts/ci/contract.py metadata
nix flake check --no-build --no-update-lock-file
nix flake check --keep-going --print-build-logs --no-update-lock-file
```

The agent draft-PR caller remains off unless `RYBSKIWORKS_AGENT_PRS=true` is deliberately configured after protections are tested. It never approves or merges PRs.

## SemVer contract

`version.txt` is the authority for this repository's tooling contract. Version the documented exported attributes, NixOS/devenv options and defaults, helper semantics, supported platform/toolchain behavior, and observable runtime/image contracts. Do not blindly classify every pin bump as a patch. Preserve the owned Fenix revision and current downstream follows graph.

Initially, fixes are patch changes, compatible additions are minor, and breaking 0.x changes require a documented minor bump with migration notes. Declaring 1.0 requires an explicit compatibility commitment. Never update a consumer's NixOS stateVersion as a routine tooling upgrade.

Release Please's root simple/version.txt strategy is a candidate for release-PR calculation. Before enabling its caller, inventory tags as well as releases and validate the first-release baseline, all-file change accounting, changelog and manifest consistency. No release calculator is enabled by this PR and no source version is treated as already published.

## Promotion to Workestrate

Workestrate already consumes a specific tooling revision and follows the shared inputs. Keep that pin unchanged until a real tested tooling release is available. Its engineering integration base is `migration/tool-model`, not legacy main.

A private-side compatibility coordinator should test the exact tooling candidate against an exact Workestrate revision and return only sanitized evidence. Never pass a token able to read private Workestrate into public nix-tooling PR jobs. Missing downstream evidence blocks release promotion.

After publication, propose an upgrade PR to Workestrate's configured integration branch, preserving follows and changing only the intended input/lock subgraph. Resolve a release to its immutable commit; a raw commit pin with recorded release metadata remains valid. The downstream PR must run its own CI before human merge.

## Publication is a separate boundary

A publisher must verify a human-approved source SHA and same-SHA full check evidence, build/test without publishing credentials, attach complete checksummed artifacts and provenance to a draft, and publish only after inventory verification. Enable GitHub immutable releases administratively before trusting tag/asset permanence. The title and release notes remain editable; attach critical metadata as a verified artifact.

This PR does not provide the private coordinator, release calculator bootstrap, publisher, native KVM runner, binary cache deployment or automatic merge policy. Their acceptance criteria and ordered rollout are in the [organization plan](https://github.com/rybskiworks/.github/blob/agents/chatgpt/governance-foundation-20260911/docs/governance.md).
