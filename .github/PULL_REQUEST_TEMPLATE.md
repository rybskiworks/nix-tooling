## Change and affected consumers

Describe the problem and affected public module/package/helper contract.

## Evidence

Record exact commands, results and tested supplier/consumer commits. Separate
ordinary-check evaluation, compilation, whole-flake shell checks and optional
native/KVM/protocol tests. List anything not run.

## Compatibility and safety

Explain input/lock changes, defaults, public interfaces, migration and rollback.
Do not initialize trackers, migrate daemons, change NixOS stateVersion or deploy
guests as incidental cleanup. Do not paste credentials or private operator logs.

- [ ] Targets `main`; no generated caches/results or machine-local input paths.
- [ ] Shared pin ownership and one-way follows are preserved.
- [ ] Public compatibility and downstream evidence are documented.
- [ ] Source version metadata is not presented as a published release.
