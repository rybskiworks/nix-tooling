# External Beads SQL server

`packages.x86_64-linux.dolt-bin` installs the official Dolt **2.1.0** Linux
amd64 release binary. This is an explicitly named binary package, not a claim
that the executable was rebuilt reproducibly from source. Its fixed archive
hash and release commit are recorded in `packages/dolt-bin.nix`. No shared
nixpkgs upgrade, default tool change, database initialization, or service
activation is implied by importing the flake.

The official binary is not admitted for TLS-enforced deployment. Its pinned
Vitess dependency writes a TLS-required error but continues the handshake;
valid credentials can therefore continue without the required transport.
This is a transport-enforcement defect, not an authentication or privilege
bypass. The earlier compliant-client refusal check below does not establish
that the server actually closes the connection.

The opt-in `packages.x86_64-linux.dolt-secure-transport` candidate retains the
same Dolt release and upstream module requirements, but uses an explicitly
patched immutable Vitess source. The patch returns immediately after the
refusal so the handler's existing cleanup closes the connection. Its Go test
uses only in-memory pipes: exact rejection and buffered-reader EOF for valid
and invalid credentials, plus an optional-transport handshake/Ping control.
The build also removes the patch from a private copy and requires that the
same regression fail specifically on post-refusal bytes before restoring and
rechecking the patched mapping. The build-local manifest explicitly includes
the package tests' `go-cmp` dependency at the version already required by the
selected Protobuf module and recorded in Dolt's upstream checksums.

The candidate's source and complete test-module NAR hashes are pinned. Its
ordinary source build passed the patched-positive, patch-removed-negative and
restored-positive regression phases:

```sh
nix build --offline --no-update-lock-file \
  --option allow-import-from-derivation false \
  --max-jobs 1 --cores 2 .#dolt-secure-transport
```

Separate disposable native SQL validation passed 62 TLS, authentication,
selected-grant, offline-account-reopening and restart observations. Both
server starts enforced actual terminal plaintext rejection; both normal stops
exited zero without escalation, with no surviving process or listener. That
result used executable SHA-256
`5b85ebd834a056e413c17feb8f7e336ee72bcbd2e626a5402081e88498906eed`.
Earlier fixture error-code mismatches remain failed runs; their correction
requires exact expected actor/database or fixed write-query scope, never a
generic SQL1105 error as a pass.

The candidate remains opt-in. It does not replace `dolt-bin` automatically,
establish actual guest service behavior or validate migration of existing
trackers. No source or dependency is vendored into this repository, and the
existing binary is not relabeled as repaired.

This version matches the external-server fixture used by the pinned Beads
1.2.2 source. Its embedded Dolt library is a separate identity. Compatibility
with an existing tracker requires a copied-backup rehearsal; successful tests
on synthetic data alone do not authorize live migration.

## Explicit checks

The ordinary `checks.x86_64-linux.beads-server-contract` runs only Python
unit tests for environment, identity, result and supervision contracts. It
does not build Dolt, launch SQL, use the network, or open a tracker.

The separate native integration check is explicitly selected:

```sh
nix build --no-link --no-update-lock-file \
  --option allow-import-from-derivation false --max-jobs 1 --cores 2 -L \
  .#legacyPackages.x86_64-linux.beadsChecks.server
```

It requires a Linux Nix sandbox with a private, loopback-only network
namespace. It refuses a host network namespace and never uses an existing
Beads directory. Each run makes a fresh private root, server data directory,
four client repository contexts, certificate authority and synthetic SQL
passwords. There are no inherited credentials, Git publication remotes,
embedded fallback, remote cache accounts, or live database mounts.

The native check's assertions cover:

- Actual pinned `bd` and Dolt versions; verified TLS with an IP SAN and private
  CA; refusal of wrong passwords, plaintext, an untrusted CA and wrong SAN.
- Offline owner-only initialization by the pinned embedded Beads engine,
  full native backup and Dolt restore into an empty server data directory
  before the listener starts. Exact schema/table inventory, project identity,
  history/branches and backup artifact hashes are compared before and after
  restore, then again through verified TLS. This is followed by SQL `_project_id`
  comparison before opening writable worker contexts. `bd context` and local
  metadata alone are not remote identity verification.
- An actual readonly account and two DML/EXECUTE accounts without DDL grants;
  two distinct actors racing to claim one issue, one acknowledged winner,
  one durable assignee/event, and a same-actor retry without a duplicate event.
  The initial loser may report the exact domain refusal or the pinned Dolt
  commit's 1213/40001 serialization rollback. One separate losing-actor retry
  must then return the domain refusal naming the winner, without changing the
  assignee or event count. This is explicit caller-managed resolution, not
  automatic retry by the unmodified Beads claim command.
- Actual `bd backup init/sync` and restoration into a new database, including
  project identity, claim and native history. Beads commits pending changes
  before backup; a separate direct `DOLT_BACKUP` check preserves a demonstrably
  dirty working set without adding a commit.
- Bounded process-group supervision and exact registered-child reaping after
  normal server termination, with primary and cleanup failures both retained.

The native check passed all eight stages with the pinned Beads 1.2.2 and
Dolt 2.1.0 binaries: the observed initial loser received 1213/40001, its
explicit retry returned the expected domain refusal, and the single claim
survived both native backup checks. The restored history contained all twelve
commits; the separate SQL backup preserved an uncommitted working set.
The server exited normally with every registered child reaped and no forced
termination. Twenty pure tests cover the fixture's negative and cleanup
oracles. These results establish this synthetic native contract, not the
separate deployment, capacity, or live-migration gates below.

The output includes a JSON stage report and redacted, bounded child logs.
The check allows 540 seconds of work plus bounded cleanup, 500 MiB allocated
scratch, 2 GiB aggregate registered-child RSS, 4 MiB per log and at least
12 GiB free disk. Exceeding a bound fails; forced termination is not normal
shutdown success. These are fixture limits, not production sizing guarantees.

The pinned Beads 1.2.2 `init --server` path does not propagate the TLS setting
to its initial connection. An actual test reached the TLS-required server and
was refused as plaintext. The offline bootstrap avoids that broken entry
point without disabling secure transport or rebuilding the client; it does
not fix direct remote initialization. The recorded native check therefore
tests subsequent server/client compatibility, not successful TLS remote init.

## Deployment boundaries

This is a native service compatibility gate, not a guest image or deployed
service. NixOS initialization, persistent mounts, guest CA distribution,
runtime secret delivery and actual guest/client routing require separate
tests. A final workload should compose the shared NixOS/Determinate base;
this package does not define a foreground-only substitute image.

An SQL credential is not task-scoped authorization. Actors remain
caller-selected, and the same actor can successfully retry a claim; this is
not an authenticated lease or exactly-once execution. SQL branch writers
may also invoke remote-management procedures. The SQL server must not hold
Git/cloud publication credentials: isolate a separate backup publisher and
apply network policy independently.

The fixture's client configuration reads its password from
`BEADS_DOLT_PASSWORD`; the pinned Beads metadata schema has no password field.
It does not populate a credential store from the environment. The test
additionally scans its own client repositories for synthetic passwords.
Real service credentials must remain runtime data, not Nix store contents.
Dolt metrics and release checking are disabled in the fixture's private
configuration; private-network isolation remains the external-traffic boundary.

The first native gate does not establish eight-client capacity, schema
upgrade/rollback compatibility with a real tracker, connection recovery,
process-crash durability, server failover, SQL revocation/rotation behavior,
or safe migration of server privileges. Preserve and verify native backups
and owner-only configuration separately from issue JSONL exports.
