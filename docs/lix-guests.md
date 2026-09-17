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
- `nixosModules.lixGuest`: explicit engine/daemon policy for a separately composed
  NixOS system. The caller still owns one shared parent and its stateVersion.
- `devenvModules.lix`: the executable client output only. It neither replaces a
  host daemon nor changes shell entry, accounts, or environment variables.

The original `guest-determinate-base`, `determinateGuest`, `determinate-nix`,
`determinate-nixd`, and `devenvModules.determinate` remain genuinely Determinate.
They are compatibility interfaces, **not aliases for Lix**. Existing consumer
pins and the tooling development shell are unchanged. Consumer adoption requires
a deliberate pin/export change and fresh image acceptance; do not switch the
engine against an existing writable store/database as an incidental module edit.

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

No Lix image boot is claimed by these source changes.
Existing Determinate VM results and `nixosImages.smokeSpec` remain specific to
their original engine; they are not Lix evidence. Before adoption, separately
review closure/download resources, inspect actual upstream units and archive
contents, then test a fresh isolated guest: registration, the selected daemon,
ordinary-user uncached builds with negative controls, daemon restart, same-store
reboot persistence, devenv use, and normal teardown. Keep existing workloads and
their state untouched until those gates and consumer pins are deliberately advanced.
