# Selectable guest engines and shared Nix stores

Status: proposed design, September 16, 2026. This document does not enable a
backend, change a default, patch an engine, or deploy an image. Nix snippets below
describe composition patterns, not newly implemented public APIs.

Implementation: [nix-tooling #15](https://github.com/rybskiworks/nix-tooling/issues/15).
Runtime/integration: [Workestrate #67](https://github.com/rybskiworks/workestrate/issues/67).
Prepared with ChatGPT from the design discussion and source inspection.

## Decision

Keep Lix and Determinate as explicit guest-image choices. Do not fork Lix just
to share store files. Prototype a frozen shared lower, kernel OverlayFS, and an
ordinary writable local Nix store with complete private metadata. Test this with
both engines before choosing a fleet default.

Keep the native `local-overlay` store implementation as a separate, optional
Determinate path. It needs its own metadata, GC, patch and remount acceptance.
Selecting Determinate is not, by itself, enabling that backend.

The three independent decisions are:

| Concern | Initial choices | Owner |
| --- | --- | --- |
| Guest engine/daemon | Lix or Determinate with its supported integration | nix-tooling and image consumer |
| Nix metadata semantics | Ordinary local store; optionally native local-overlay | Guest engine module |
| Filesystem presentation | Private filesystem or kernel OverlayFS | Runtime and guest boot integration |

Whole-VM disk CoW is a future runtime capability, not another invented Nix store
URL. KSM is a separate memory-sharing decision. Neither is a prerequisite for
sharing immutable store files now.

### Why this differs from the preliminary proposal

The absence of `local-overlay-store` in Lix does not prohibit using Linux
OverlayFS beneath an ordinary Lix local store. The native backend provides lazy
lower-metadata lookup and special GC/deletion semantics; it is not the kernel
filesystem implementation.

microvm.nix is a concrete reference: its [mount module][microvm-mounts] uses
`fileSystems."/nix/store".overlay`, and its [store module][microvm-store] loads
closure registration into the guest database. Its [sharing guide][microvm-shares]
also documents writable overlays and warns about registrations being forgotten
across reboot. That is evidence for the design pattern, not a passing test of
our Lix/Microsandbox combination.

For a fixed, completely enumerated lower, we can seed one ordinary private DB
and keep all lower objects rooted. This avoids maintaining a Lix backend port.
The price is metadata duplication, explicit generation lifecycle management, and
retaining the whole lower generation until its consumers release it.

## Current repository boundaries

The inspected tooling main revision is
`a403c2c111e24db64feb5748bbba939308048c07`. It provides the Determinate guest
profile and common registered-image machinery.

The inspected fleet instead pins tooling
`e34328e28375f512d014af2424e98741e7c7929b`, selects `guest-lix-base`, and passes
that base to its workload builders. Do not mistake a fleet's pinned branch
revision for tooling main. Preserve and reconcile existing Lix work, including
[the Lix smoke PR][lix-smoke], rather than duplicating or overwriting it.

Relevant existing interfaces:

- `lib/guest/default.nix`: explicit package-set injection and runtime-neutral
  image/system constructors.
- `lib/guest/nixos-image.nix`: image closure metadata, base/leaf composition and
  `guestRegistration` passthrough.
- `lib/guest/register-store.sh`: local DB initialization, registration and roots.
- `nixosModules/microsandbox-guest.nix`: registration readiness and daemon/socket
  ordering for a kernel supplied by Microsandbox.
- `nixosModules/determinate-guest.nix`: explicit engine/supervisor and security
  policy, without installing a host daemon.

Workestrate already depends on nix-tooling. The dependency must not be reversed
merely to calculate a closure containing Workestrate:

```text
nix-tooling: reusable helpers, engine pins and modules
       ^                         ^
       |                         |
Workestrate packages       workload package suppliers
       |                         |
       +----------+--------------+
                  |
fleet/consumer: selects roots, engine and image variants
                  |
Workestrate runtime: realizes, attaches and leases the generation
```

## Engine selection: ordinary pure Nix composition

Keep the explicit `guest-lix-base` and `guest-determinate-base` package outputs.
Once the existing Lix branch is integrated, a fleet can select the base once:

```nix
{ tooling, system, engine }:
let
  bases = {
    lix = tooling.packages.${system}.guest-lix-base;
    determinate = tooling.packages.${system}.guest-determinate-base;
  };
in
bases.${engine} or (throw "Unsupported guest Nix engine: ${engine}")
```

The fleet's existing workload constructor then receives that `base`. Build
explicit named variants from one constructor rather than duplicating the whole
fleet definition. A flake is not an arbitrary CLI parameter function: expose
package variants, or make the selection in the consumer's committed Nix config.
Do not select it using `builtins.getEnv`, `--impure`, or shell-entry side effects.

If consumers need to customize an engine base, use the existing
`lib.guest.mkNixosImage` with a selected engine module. Add a small common
constructor only when it removes real duplicated configuration. Do not replace
the generic image API with a Workestrate-only DSL.

Choose the module before NixOS evaluation, or import static modules whose
configuration is conditional. Avoid imports selected from `config`, which can
introduce module-evaluation recursion. Factor common security/cache settings
without enabling two supervisors together.

Preserve public output names and default identities. A default-engine change is
a separate rollout PR after tests, not an incidental refactor. Keep both variants
available for rollback. Changing a shell's `nix` executable must not migrate the
host daemon, and the host need not use the same engine as every guest.

Package overlays and OverlayFS are unrelated mechanisms. Use package overlays
only for actual package selection/patching. Avoid a global `pkgs.nix` replacement
or a `pkgs.workestrate` attribute set that collides with the Workestrate package.
Preserve supplier package identities where no rebuild is required.

## Choosing the shared contents

A repository or flake is not itself a derivation. The consumer selects concrete
outputs from nix-tooling, Workestrate, workloads and toolchains. Runtime
references pull in Microsandbox/libkrun only when the selected derivations
actually reference them; otherwise include those outputs explicitly.

Reuse `pkgs.closureInfo` and the registration metadata already produced by the
image library. A composition pattern is:

```nix
{ pkgs, selectedPackageOutputs, imagePayloadRoots }:
pkgs.closureInfo {
  rootPaths = selectedPackageOutputs ++ imagePayloadRoots;
}
```

`imagePayloadRoots` must cover the selected base and all selected leaf payloads,
not merely an OCI archive output. Reuse each relevant image's
`guestRegistration.roots`. The existing image builder treats runtime config as a
root too; retain that behavior rather than checking only payload executables.

The [pinned closureInfo implementation][closure-info] already emits:

```text
registration       metadata suitable for nix-store --load-db
store-paths        complete transitive path inventory
 total-nar-size    logical NAR byte total (filename has no leading space)
```

Do not write a second closure traversal or a union-SQLite implementation. Add a
small versioned controller envelope only for the identity, provenance, platform
and lifecycle fields not represented by these outputs.

### Runtime closure versus development warming

The closure of a built output is not its complete build dependency closure.
Agent images that edit derivations also benefit from realized compiler/toolchain
outputs, development shells, build inputs and selected source/derivation paths.
Specify this as an additional root set.

`nix-store --query --requisites --include-outputs` can inventory derivations and
outputs already present. It does not build every missing output. A cache hit for
a final binary also does not guarantee that its build-only dependencies are
locally realized. Make build-input realization an explicit provisioning step,
then freeze the exact resulting inventory. Missing future versions still build
or substitute into the guest's writable upper.

Avoid making an entire historical toolchain/bootstrap graph mandatory without
measuring it. Record path count, NAR size, actual allocated base size and cache
misses separately. No fixed percentage of storage savings is guaranteed.

## Generation artifact and host reuse

The host realizes the selected roots using its normal store, builders and
substituters. Host profiles and development shells can use those exact package
outputs. The generation materializer separately creates the VM's immutable view.

```text
normal host /nix/store
       |
selected roots + complete closure registration
       |
sealed generation G
       +------ read-only attachment ------ VM A + private state A
       +------ read-only attachment ------ VM B + private state B
```

A list of paths is not an immutable filesystem. The materializer must produce a
frozen export or immutable image whose visible entries exactly match the
validated closure. Prefer an existing immutable-image or confined filesystem
export facility supported by the runtime. Use ordinary kernel OverlayFS for the
union. Do not build a custom filesystem server just to select paths.

Never attach the live workstation's entire `/nix/store`, host DB or daemon
socket. GC roots stop deletion; they do not stop additions to a live directory,
repair, or other mutations. The [kernel contract][overlayfs] disallows underlying
filesystem changes while an overlay is mounted.

Symlinks escaping back into the host store are not an isolated generation.
Hardlink/export schemes also need to prevent later underlying inode mutation.
A read-only guest mount alone is not enforcement against a privileged guest:
read-only access must also be enforced by the host export or device boundary.

The same logical store paths do not prove zero physical duplication between the
host and a materialized image. Record that cost honestly. Sharing one generation
among many VMs is still useful if materializing it requires a separate copy.

Use immutable generation identities and leases. Acquire a lease before launch;
retain it for a persistent stopped VM and until a terminated VM's attachments are
released. Reconcile state after controller failure instead of assuming a missing
heartbeat proves that the VMM is dead. Publish a generation atomically only after
its inventory, references, content/provenance and boot closure are validated.

## Preferred prototype: ordinary local store over OverlayFS

```text
read-only generation G/store ---- lowerdir ----+
                                              |
private volume/store ----------- upperdir -----+---- /nix/store
private volume/work ------------ workdir ------+

private volume/state ------------------------------ /nix/var/nix
    db/
    gcroots/
    profiles/ and other owned persistent state
```

The DB is writable and belongs only to that VM. This is not Lix's
`read-only-local-store` mode. The shared lower is read-only; the merged logical
store remains writable. Clients use the ordinary guest daemon and can realize
new `.drv` paths, sources, dependencies and outputs.

Keep upper and workdir on the same supported filesystem, with no overlapping or
shared upper/work assignment across VMs. Prefer a supported local volume for the
writable side initially. Do not infer that virtiofs is a valid upper merely
because it works as a lower. Make kernel/filesystem capability checks explicit.

### Registration and GC invariants

Before starting any daemon or accepting clients:

1. Validate the launch's generation, image, engine and private-state identities.
2. Establish the merged store and private writable state mounts.
3. Initialize a fresh DB only for a fresh state generation. Register every lower
   object from trusted generation metadata, then any additional image payload.
4. Install persistent guest GC roots covering **all** lower paths. Verify every
   registered reference exists in the merged namespace.
5. Mark readiness only after all checks pass; then start daemon/socket activation.

Extend the existing registration service rather than inventing a second one.
Its `NIX_REMOTE=local` is intentional for this mode. Serialize registration, and
ensure daemon/sockets cannot race a half-imported database.

A simple initial rooting scheme is one validated root per lower store path,
under a generation-specific GC-root directory. An equivalent aggregate root is
acceptable only with tests that prove its full reference retention. A generated
manifest file is not automatically a durable GC root.

Complete registration alone is insufficient: normal GC may delete registered but
unrooted paths. Rooting every lower object prevents ordinary guest GC from
whiteouting those objects and treating their logical size as private reclamation.
All upper-only garbage can still be collected normally.

Disable scheduled optimisation and `auto-optimise-store` initially. Characterize
explicit optimisation and repair: metadata changes, hardlinking and repair can
copy lower objects into the upper. They must not modify the physical lower.
Physical host allocation and guest GC accounting need separate measurements.

A privileged guest can deliberately remove its own roots or damage its overlay
view. The security guarantee is confinement of that damage, not that guest root
cannot make its own VM unusable. Untrusted ordinary Nix clients must retain the
existing restricted-setting and daemon authorization policy.

### Persist or discard the complete state

Bind upper bytes, DB, GC roots and generation identity together. Restarting with
an old upper and a fresh unrelated DB loses registrations; switching the lower
under a retained upper can expose stale files or whiteouts. Neither is an upgrade
strategy.

For ephemeral guests, discard the complete state after quiescence. For persistent
guests, retain the full state and its lower lease. Refuse a partial or mismatched
state instead of silently reinitializing it. Engine/backend changes get a fresh
state unless a compatible migration has been explicitly tested.

## Boot integration is the main runtime prerequisite

Our image's `/init` points into its Nix store. Microsandbox supplies the kernel
and hands off to that executable; it does not automatically run a newly declared
NixOS initrd. A valid NixOS `fileSystems` option is not proof of a valid boot path.

Use the runtime's existing mount/volume capabilities and the smallest explicit
pre-init handoff contract. The merged namespace must contain the exact init,
loader, engine, registration tooling and base/leaf runtime closure before init
is executed. Do not hide an image's existing store with an incomplete shared
lower. Preserve agentd's existing `/run`, network and CA handoff contracts.

For NixOS backends that genuinely execute their own initrd, reuse the declarative
filesystem interface, conceptually:

```nix
fileSystems."/nix/store".overlay = {
  lowerdir = [ "/nix-base/store" ];
  upperdir = "/nix-private/store";
  workdir = "/nix-private/work";
};
```

This snippet alone is not a Microsandbox implementation. Backing mounts,
creation/permissions, boot ordering and readiness must all be established and
validated on the actual supported runtime. Avoid switching a live daemon's store
mount underneath it.

## Optional native local-overlay backend

Use a capable pinned engine, independently of the filesystem-overlay prototype.
The native backend understands lower/upper metadata and special deletion rules.
It must receive a correctly mounted filesystem, an explicit lower-store URI,
upper-layer path and private state. Keep mount checking enabled.

Do not copy a running host SQLite database. Build compatible lower metadata from
trusted registration, or a consistent sealed store, and enforce immutability for
both metadata and files. Distinguish the lower store URI from the writable store
URI, including any required encoding of nested URI parameters.

Boot registration, daemon service, maintenance commands and clients must select
the same intended backend. Enabling `local-overlay-store` merely permits the
feature; it does not change a daemon from `local` to `local-overlay`. The existing
registration script hardcodes `local`, so native selection needs deliberate
adaptation and tests. Verify the exact pinned Determinate/Nixd supervision
arguments instead of assuming a client environment variable controls the daemon.

[Nix issue #16269][gc-bug] documents an uninitialized byte count in overlay GC.
Check the exact pinned engine source. If affected, apply the narrow initialization
fix through that source's supported component packaging, with a finite-target
regression test. Do not patch only an aggregate wrapper output. Make patch drift
fail the build, so an upstream fix causes review/removal of the downstream patch.
The bug is undefined behavior: an unlimited collection is not a correctness fix.

The [native backend documentation][native-overlay] describes a remount requirement
when deleting a redundant upper copy that also exists below. Do not implement a
blind lazy unmount or mutate a live upper directly as a workaround. Establish
quiescence, namespace propagation and correct remount behavior, or leave the
native backend disabled. The fixed-generation ordinary-local prototype does not
need these native-backend operations.

Disable all automatic collectors, including supervisor-specific background GC,
until the native finite/automatic GC gates pass. This does not remove the need
for storage quotas and explicit out-of-space behavior.

## Cachix and builders

Use the existing cache-policy composition, not a new package universe. Tooling
owns reviewed pins and shared cache defaults; consumers choose which source and
workload closures may be published. Use actual cache public keys. Keep private
cache credentials outside store/image outputs and deliver them through existing
runtime secret controls.

There are two independent uses:

- Provisioning: builders and substituters realize a generation once for the host.
- Runtime: a missing object is substituted or built and imported into a VM's
  writable upper. Nothing writes into the shared lower.

Do not promise that a remote-builder output automatically appears in Cachix;
publishing is a separately configured operation. Do not publish every observed
build-store object to a public cache when private sources/configuration may be
present. Use explicit approved push roots and cache-specific permissions.

## Acceptance and rollout

Each evidence level must be reported separately. Documentation review, Nix
module evaluation, QEMU/NixOS VM tests and actual Microsandbox tests establish
different things. A passing fixture is not proof of an untested transport.

Required gates before changing the fleet default:

- Both engine profiles evaluate without mixed daemons, changed supplier identities
  or hidden host effects. Invalid engine/backend selections fail clearly.
- Generation metadata is deterministic and reference-complete. Missing payload,
  invalid roots and private-publication mistakes are rejected.
- Two real supported microVMs share one frozen lower with separate upper/DB/root
  state. Each performs an uncached sandbox build, changed derivation build,
  `nix develop`, `nix flake check`, substitution and a remote build.
- Lower files/metadata remain unchanged; one VM cannot write another's state or
  reach host-only files. A guest-root lower-write attempt is denied at the export
  boundary, not merely by a cooperative client.
- Dead upper paths are reclaimed, lower paths survive GC, and byte accounting is
  sensible. Native mode separately covers lower-only paths, finite/automatic GC,
  duplicate copies and remount behavior.
- Daemon restart, guest restart, normal shutdown, forced termination, disk-full
  conditions and controller recovery preserve or explicitly discard complete
  state. Missing mounts, stale generations and DB mismatches fail closed.

Implementation order:

1. Reconcile pending Lix work and expose engine variants without a default change.
2. Reuse closureInfo/image metadata; implement the sealed materializer and leases.
3. Implement runtime mount handoff and complete guest registration/rooting.
4. Run the ordinary-local matrix with both engines. Choose the first supported
   default based on that evidence, not feature-name matching.
5. Add native local-overlay only if its benefits justify its additional gates.
6. Configure cache publication/builders, then promote tested tooling revisions
   through separate fleet and personal-config PRs.

Do not close the integration tracker when this document merges. No consumer pin,
host installation, NixOS stateVersion or existing persistent store is changed by
this proposal. Keep a tested private-local-store image for rollback; never reuse
an incompatible overlay DB simply because an image variant has a similar name.

Later disk CoW can provide a different filesystem presentation while preserving
engine optionality. Whether it replaces shared store generations should depend
on measured duplication, update and lifecycle behavior, not an assumption that
all forms of sharing become redundant. KSM remains an independently reviewed
memory/trust-boundary feature.

## References

[microvm-mounts]: https://github.com/microvm-nix/microvm.nix/blob/187b0a390ee054028106a674e7b01b1cb940cbba/nixos-modules/microvm/mounts.nix
[microvm-store]: https://github.com/microvm-nix/microvm.nix/blob/187b0a390ee054028106a674e7b01b1cb940cbba/nixos-modules/microvm/store-disk.nix
[microvm-shares]: https://microvm-nix.github.io/microvm.nix/shares.html
[lix-smoke]: https://github.com/rybskiworks/nix-tooling/pull/14
[closure-info]: https://github.com/NixOS/nixpkgs/blob/a799d3e3886da994fa307f817a6bc705ae538eeb/pkgs/build-support/closure-info.nix
[overlayfs]: https://docs.kernel.org/filesystems/overlayfs.html
[native-overlay]: https://nix.dev/manual/nix/2.34/store/types/experimental-local-overlay-store
[gc-bug]: https://github.com/NixOS/nix/issues/16269
