# Supported Determinate guests

`nixosModules.determinateGuest` is an explicit guest capability profile. It
imports the official Determinate NixOS module, selecting its matched
open-source Nix engine and binary-only Nixd supervisor together. It does not
install or migrate the host daemon, activate a guest, create an account, start
a hosted builder or publish an image. Default tooling packages, shared
development-shell modules and minimal-image contents remain unchanged.

```nix
inputs.tooling.lib.guest.mkNixosSystem {
  inherit pkgs;
  stateVersion = "26.05";
  modules = [
    inputs.tooling.nixosModules.determinateGuest
    { networking.hostName = "development-guest"; }
  ];
}
```

The result is an ordinary NixOS evaluation, not a bootable OCI artifact.
Consumers still own image assembly, writable store/database state, users,
activation/init, networking, readiness, resources and teardown. Adopting the
same profile across workload images requires verifying those capabilities in
each image/runtime combination. A service image that merely contains a Nix
executable does not establish a working multi-user daemon or NixOS activation.

## Immutable package ownership

The official distribution is pinned to
`cb76ac22754f6b36c008a3c39477c174a146dd6b` (3.22.3); its engine source is
`3ed5caa3bbde51ab87977c8a80e27c6ed0ea8534`. Explicit package outputs avoid
confusing the supplier's default Nixd package with the engine:

- `packages.x86_64-linux.determinate-nix`: the official module's engine package.
- `packages.x86_64-linux.determinate-nixd`: the matched official supervisor.

The supplier's two locked package sets are deliberately preserved. Its engine
uses nixpkgs `c5c4a43b0e8056328ec4529f735cabdb8f1942bb`; its Nixd wrapper uses
`56c02bc00adcf003215cc4bd996d6efaf4cff188`. Do not force them to follow the
consumer's nixpkgs: that changes the supplier's cached derivations. NixOS and
other guest packages continue using the caller's explicit shared `pkgs`.
This is a supplier-cache exception, not a second fleet package authority.

The engine's open-source license does not describe the separately supplied
binary-only Nixd. Review applicable distribution terms before public image
redistribution. Local validation needs no configured hosted account or paid cache;
private inputs, hosted services and signing credentials are separate choices.
See the [official integration source](https://github.com/DeterminateSystems/determinate/blob/cb76ac22754f6b36c008a3c39477c174a146dd6b/README.md).

## Configuration and privacy

The profile enables normal NixOS daemon/build-user integration with sandboxing,
signature checks and root-only trusted users. Ordinary members of `users` may
submit builds without gaining daemon trust. It disables channel updates and
automatic upgrades, pins the nixpkgs registry to the shared input and disables
the floating global registry. Automatic NixOS GC/optimisation and Nixd GC are
disabled; deployment must supply an explicit retention/quota policy.

Client sessions and the daemon receive `DETSYS_IDS_TELEMETRY=disabled` and an
empty `NIX_SENTRY_ENDPOINT`; Nixd's crash endpoint is null. No extra netrc files
are combined. The macOS-specific hosted Linux builder is disabled; it is not
the Linux guest's own daemon. These settings are declarative intentions, not
packet-capture evidence. Nixd still attempts background control-plane
authentication without a configured account; no account setup and disabled
telemetry do not mean no network attempts. Disabling telemetry may disable supplier feature
rollouts. [Official telemetry controls](https://docs.determinate.systems/guides/telemetry/),
[Nixd configuration](https://docs.determinate.systems/determinate-nix/determinate-nixd/).

The shared profile retains NixOS's public cache and adds the official
`https://install.determinate.systems` cache with its public verification key.
This does not grant network egress: deployment policy independently controls
reachability. Standard NixOS options can add a reviewed signed cache and change
the registry without overriding the module's priority:

```nix
{
  nix.settings.substituters = [ "https://cache.example.org" ];
  nix.settings.trusted-public-keys = [ "cache.example.org-1:<public-key>" ];
  nix.registry.nixpkgs.flake = inputs.myPinnedNixpkgs;
}
```

Nixd generates the active `nix.conf`, includes `nix.custom.conf`, then appends
its install-cache URL, trusted install/FlakeHub cache URLs and supplier keys.
Consequently, empty `nix.settings` lists do not remove these managed additions;
the static contract checks declarative option values, not an exclusive runtime
cache policy. Nixd also supplies a legacy floating `extra-nix-path` before the
custom include. The pinned flake registry is not proof that every legacy
`<nixpkgs>` lookup uses that registry.

These are additive cache lists, not replacement or read credentials. Keep
private reader credentials outside the Nix store; never provide signing or
upload keys to general workloads. `determinate.edgeCacheSubstituters` is not
the configuration interface used by this profile; use `nix.settings` for cache
composition. SSH daemon access is not a read-only cache and needs its own
privileged-builder trust boundary.

## Checks and resource admission

The small `checks.x86_64-linux.determinate-contract` evaluates package/module
identity, package-set injection, declarative privacy/trust settings and standard
consumer cache/key/registry composition. It does not build the engine, inspect
binary behavior or boot NixOS. The separate daemon test is deliberately outside
ordinary `checks`, so normal tooling verification cannot launch it:

```sh
nix eval --no-update-lock-file --option allow-import-from-derivation false \
  .#checks.x86_64-linux.determinate-contract.drvPath
nix build --dry-run --no-link --no-update-lock-file \
  .#determinate-nix .#determinate-nixd
nix build --dry-run --no-link --no-update-lock-file \
  .#legacyPackages.x86_64-linux.determinateChecks.nixos
```

The supplier cache can be enabled for a single reviewed build without changing
host configuration:

```sh
nix build --no-link --no-update-lock-file --max-jobs 1 --cores 2 \
  --extra-substituters https://install.determinate.systems \
  --extra-trusted-public-keys 'cache.flakehub.com-3:hJuILl5sVK4iKm86JzgdXW12Y2Hwd5G07qKtHTOcDCM=' \
  .#determinate-nix .#determinate-nixd
```

Review exact dry-run paths and resource headroom first. A cache miss must not
silently trigger a large supplier compiler build. Missing `FileSize` in cache
metadata can produce a zero-byte Nix download estimate; it does not mean the
closure is free to acquire.

The opt-in NixOS test defines a 2-CPU/2-GiB guest, an 8-GiB writable disk and a
600-second driver limit. Its own store image is read-only with a guest-disk
upper layer, not a mounted host store or daemon. It records the effective
managed supplier URLs/keys and disables substitution for its build requests
with test-only `substitute=false`. That is a request mode, not an administrative
ban on another client's substitution choices. Restricted guest networking
provides the external-egress boundary. It requires absent outputs before normal
untrusted-user builds. Declared-input and outside-file/network positive
controls accompany sandbox denials; attempted client overrides must be ignored.
It maps the kernel-reported namespace UID to the guest's dedicated build-user
UID, verifies actual daemon handshake trust, restarts the daemon, roots the outputs and
reboots the guest before verifying its persistent outputs, then shuts down.

The pinned test passed on x86_64 Linux with all six subtests, including both
uncached builds, sandbox controls, daemon restart and same-disk reboot/output
verification followed by normal shutdown. Namespace UID 1000 mapped to the
guest's dedicated `nixbld` UID 30001. The actual daemon reported the guest
untrusted, root trusted, and the guest still untrusted after a client-side
`trusted-users` override. Sandbox and signature overrides produced explicit
rejection warnings; `trusted-users` is a separate daemon authorization setting,
not one of those transmitted build options.

The result is `/nix/store/53filg9f9jkfzcy8pxlp7cb890kv015r-vm-test-run-determinate-guest-daemon`.
QEMU used TCG after reporting unavailable KVM access, so this establishes
standard NixOS behavior under emulation, not hardware acceleration. Run the
opt-in test only after reviewing its resource plan:

```sh
nix build --no-link --no-update-lock-file \
  --option allow-import-from-derivation false --max-jobs 1 --cores 2 \
  .#legacyPackages.x86_64-linux.determinateChecks.nixos
```

Ignoring an untrusted `require-sigs=false` setting is not an invalid-signature
admission test. Signed-cache negative tests, interrupted registration recovery,
SSH builds, Microsandbox PID1/activation/shutdown, nested VMs and real workload
coding remain distinct gates. Standard NixOS success cannot substitute for any
of those runtime contracts.
