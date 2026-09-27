# Shared Lix guests

`packages.x86_64-linux.guest-lix-base` is the common parent for new service and
agent images. It uses NixOS container userspace, the shared package set's stable
Lix package, and the public devenv cache. Leaves add application packages through
`lib.guest.mkNixosLayer`; they must not replace the parent's engine, system,
registration database, or `/etc/nix` configuration.

```nix
tooling.lib.guest.mkNixosLayer {
  inherit pkgs;
  base = tooling.packages.${pkgs.system}.guest-lix-base;
  name = "my-service";
  tag = "current";
  registrationName = "my-service";
  contents = [ myService ];
  config.Cmd = [ "${myService}/bin/my-service" ];
}
```

## Package and module ownership

The pinned nixpkgs revision `a799d3e3886da994fa307f817a6bc705ae538eeb`
selects `pkgs.lixPackageSets.stable.lix`, currently **2.94.2**. This is the stable
selection at that immutable revision, not a claim to track the newest Lix release.
No separate rolling Lix input or installer is used. Lix recommends the nixpkgs
package for stable NixOS installations; its external NixOS module is intended for
bleeding-edge builds. See [official NixOS configuration guidance](https://lix.systems/add-to-config/).

Public interfaces are:

- `packages.x86_64-linux.lix`: the exact shared stable engine.
- `packages.x86_64-linux.guest-lix-base`: its registered common image parent.
- `nixosModules.lixSystem`: shared engine/daemon policy for hosts and guests,
  without container, boot, network, firewall or maintenance settings.
- `nixosModules.lixGuest`: imports `lixSystem` and adds disabled automatic GC/
  optimisation plus default one-job/two-core limits. The caller still owns one
  shared parent and its stateVersion.
- `devenvModules.lix`: the executable client output only. It neither replaces a
  host daemon nor changes shell entry, accounts, or environment variables.

The original `guest-determinate-base`, `determinateGuest`, `determinate-nix`,
`determinate-nixd`, and `devenvModules.determinate` remain genuinely Determinate.
They are compatibility interfaces, **not aliases for Lix**. Existing consumer
pins and the tooling development shell are unchanged. Consumer adoption requires
a deliberate pin/export change and fresh image acceptance; do not switch the
engine against an existing writable store/database as an incidental module edit.

The role-neutral module defaults to the caller's `pkgs.lixPackageSets.stable.lix`,
including its overlays. A consumer can set `nix.package = selectedLix` to reuse
an exact package output across hosts and guests. The module rejects non-Lix
packages and packages whose declared platforms exclude the consumer's host.
These metadata checks do not replace native daemon and image qualification of
a newly selected version. The existing 2.94.2 daemon-template handling follows
the selected package's version.

The module does not globally replace `pkgs.nix`, install guest registration units
on a host, or grant access to another system's store/database. Its nixpkgs
registry still defaults to tooling's pinned source for compatibility; consumers
owning that source should also set `nix.registry.nixpkgs.flake = inputs.nixpkgs`.
Host and guest package identity alone does not establish shared physical storage.

For a host, import `nixosModules.lixSystem` directly and supply hardware, user,
network, maintenance and resource policy in the host's own modules. The ordinary
`lix-system-contract` checks this separation, the default host/guest engine
identity, caller package-set and explicit engine selection, rejection of non-Lix
and incompatible-platform packages, and composition with the pinned
[SOPS modules](sops.md). Runtime host qualification
remains separate from this evaluation contract.

## Daemon, store, and isolation

The selected Lix 2.94.2 package supplies `nix-daemon.service` and
`nix-daemon.socket`, not a per-connection `nix-daemon@` template. The pinned
NixOS module nevertheless declares template overrides; this profile masks that
unsupported template for exactly 2.94.2 to avoid emitting a unit without
`ExecStart`. A future stable-package change must recheck the actual unit inventory.
NixOS supplies dedicated `nixbld` users. There is no Determinate Nixd supervisor, custom-config include, managed
FlakeHub cache, account configuration, or Determinate telemetry environment.
The active daemon and socket depend on guest store registration; the daemon
receives the final runtime-delivered CA bundle location. The generic constructor
also retains these guards for Lix packages that supply a daemon template.

The profile enables flakes and `nix-command`, signed substitutions, sandboxing
without fallback, root-only trusted users, and ordinary build requests by `users`.
It disables automatic channel updates, upgrades, GC and optimisation, pins the
nixpkgs registry, and disables automatic flake trust. The common parent contains
only the NixOS and public devenv caches with their signing keys. These settings
do not grant egress, introduce credentials, or establish a resource-retention policy.

The generic [NixOS image contract](nixos-oci-images.md) still governs `/init`,
volatile `/run`, runtime-owned networking/CA files, additive registration, and
guest-owned writable store state. Registration invokes the selected package's
`nix-store`; it never mounts or rewrites the host database or daemon socket.
`guest-store-ready.target` alone does not prove a live daemon handshake or a
sandbox boundary. Root-submitted builds are not evidence of untrusted-client
isolation.

## devenv compatibility boundary

The locked devenv source is `97135e80b6e432f41f84f72383e1b8147f33ef0c`
(2.2.3). Its packaged CLI embeds its supplier's Nix C API; selecting a Lix daemon
or client on PATH does not turn those libraries into Lix. Keep devenv's owned
Nix input and task-package identities intact. Do not globally replace `pkgs.nix`
or override its C API dependency with Lix. The same distinction applies to tools
that embed Nix libraries: use reviewed Lix package-set variants only where needed,
as described in the official configuration guidance.

The declarative devenv modules remain ordinary Nix expressions. Actual
`nix develop`, devenv CLI evaluation/building, caching, and daemon communication
must still be exercised against the selected guest pair. Lix 2.94 removed daemon
protocols older than Nix 2.18; do not infer support for every existing remote
builder or legacy client from package evaluation.
See [Lix 2.94 release notes](https://docs.lix.systems/manual/lix/nightly/release-notes/rl-2.94.html)
and the [pinned devenv package definition](https://github.com/cachix/devenv/blob/97135e80b6e432f41f84f72383e1b8147f33ef0c/flake.nix).

## Verification and adoption

The ordinary `lix-contract` evaluates engine identity, client-only module scope,
upstream daemon integration, registration/CA ordering, trust/cache policy,
consumer composition, and exact base-to-leaf inheritance. It does not realize
Lix, an image, or a VM. The native version check and example leaf are opt-in:

```sh
nix eval --offline --no-write-lock-file --option allow-import-from-derivation false \
  --raw .#checks.x86_64-linux.lix-contract.text
nix build --dry-run --no-link --no-write-lock-file \
  .#legacyPackages.x86_64-linux.lixChecks.client
nix build --dry-run --no-link --no-write-lock-file \
  .#legacyPackages.x86_64-linux.lixChecks.image
```

The optional image check realizes the parent, verifies the emitted plain daemon
and socket against the exact package, checks registration/CA overrides, and
requires the unsupported template to resolve to `/dev/null`.

Source checks alone do not prove a Lix image boot. The existing independent
runner accepts the metadata-only `legacyPackages.x86_64-linux.lixChecks.smokeSpec`
with `--mode build-persistence`. Its version-two spec explicitly selects Lix:
`nix store ping --json`, the plain daemon/socket, the exact daemon executable,
the masked unsupported template, and the exclusive NixOS/devenv cache list.
Pass `--runtime-version 'msb 0.6.18'` with the matching reviewed current runtime
hashes; the old version-one spec and CLI version default remain unchanged.
Lix mode requires 25 GiB initial free disk, retains a 20 GiB floor and 8 GiB
available-memory floor, and keeps the existing 4 GiB scratch/global-growth limits,
300-second work plus 90-second cleanup budgets, and one-CPU/2-GiB guest.

This mode requires the real root/untrusted daemon handshakes, two uncached builds
including restricted-setting overrides, daemon restart, same-store VM restart,
both normal poweroffs, healthy boot units, and complete cleanup. Its success is
`build_persistence_passed`, **never** `full_acceptance_passed`; the report records
`full_acceptance: false` and `denied_client_control: not_run`.
Cleanup omits a repeated stop only after this runner has validated the current
launch's normal exit and flush, then freshly observed no owned process. Every new
launch attempt invalidates that evidence. This does not establish native
repeat-stop idempotence or permit cleanup from terminal status alone.

Lix 2.94.2 rejects a disallowed peer in a per-connection subdaemon before its
protocol handshake. A client EOF/broken pipe with partial `{"url":"daemon"}`
JSON is not authorization evidence. Lix full/activation modes are therefore
refused before creating a VM until a journal-correlated disallowed-client control
is implemented. That later control must bind a fresh cursor, unit invocation and
the unique test peer; it must not reuse Determinate's client error predicate.
See the [pinned daemon source](https://git.lix.systems/lix-project/lix/src/tag/2.94.2/lix/nix/daemon.cc)
and [ping implementation](https://git.lix.systems/lix-project/lix/src/tag/2.94.2/lix/nix/ping-store.cc).

Existing Determinate VM results and `nixosImages.smokeSpec` remain specific to
their original engine; they are not Lix evidence. Before adoption, separately
review closure/download resources, inspect actual upstream units and archive
contents, then test a fresh isolated guest: registration, the selected daemon,
ordinary-user uncached builds with negative controls, daemon restart, same-store
reboot persistence, devenv use, and normal teardown. Keep existing workloads and
their state untouched until those gates and consumer pins are deliberately advanced.
