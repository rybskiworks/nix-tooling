# NixOS OCI images with a guest-owned Nix store

The experimental `packages.x86_64-linux.guest-determinate-base` output is one
shared NixOS userspace archive, with the supported Determinate Nix/Nixd pair.
It does not build a kernel, boot a VM, activate a host service, or initialize
Beads. The runtime supplies the kernel and must explicitly invoke the generated
NixOS init. Base and example-leaf assembly and read-only archive checks have
passed on x86_64-linux. Microsandbox activation has not been tested; the
separate standard NixOS VM result is not that proof.

All service and agent leaves should consume the same pinned base output:

```nix
tooling.lib.guest.mkNixosLayer {
  inherit pkgs;
  base = tooling.packages.${pkgs.system}.guest-determinate-base;
  name = "my-service";
  tag = "current";
  registrationName = "my-service";
  contents = [ myService ];
  config.Cmd = [ "${myService}/bin/my-service" ];
}
```

`pkgs` remains an explicit consumer-owned package set. The base uses tooling's
shared package set; the official supplier's engine and Nixd package identities
and locks remain separate. Leaves extend this archive through `fromImage`;
they do not evaluate their own NixOS system or install a different engine.
Application configuration, credentials, mounts and service lifecycle still
belong to the workload. Never place credentials in an image derivation.

For an explicitly customized common base, `lib.guest.mkNixosImage` accepts
`pkgs`, `name`, `stateVersion`, optional `tag`, `modules` and `maxLayers`.
It imports `nixosModules.microsandboxGuest`; callers must also choose an engine
profile, normally `nixosModules.determinateGuest`. Do not create independently
customized bases for each leaf when one shared image is required.

## Initialization and readiness

The archive exposes `guestInit`, `guestSystem`, `guestToplevel`,
`guestRegistration`, `guestRegistrationNames` and `guestLayerBudget`, and
retains dockerTools' `.stream`. `guestInit` describes `/init`, empty arguments, workdir `/`, and
`container=microsandbox`. `/init` links to the generated system toplevel's
stage-2 init. It is not an application Entrypoint or a direct systemd shortcut.
The consumer must translate this descriptor to its explicit runtime init API.

The runtime configures network interfaces, hostname, resolver, gateway hosts
and optional CA certificates before handoff. The module leaves those files
and interfaces runtime-owned while retaining ordinary NixOS activation for
users, NSS and units. Before executing systemd, it requires `/run` to be an
existing tmpfs mount so systemd does not hide pre-handoff files under a fresh
filesystem. The producer must mount it before writing runtime files, after
any root pivot. An unmounted or persistent-overlay `/run` is rejected, not
self-bound: that would preserve stale state and break volatile runtime-directory
lifetime. Runtimes that only create `/run` as a directory are not compatible
with this profile until their bootstrap is corrected. No host bind or process
survival exemption is used.

The image's public CA bundle is a regular writable file. Agentd may append its
runtime-delivered CA; activation does not replace it with a store symlink.
Nix daemon and session CA variables point to that final bundle. No runtime
certificate is baked into the image. An actual guest test must check its
contents and the preserved control connection after activation.

`guest-store-registration.service` must complete before sysinit and both
daemon sockets. It loads every image registration into the local guest
database and creates GC roots on every boot. `guest-store-ready.target` means
registration completed; it does **not** prove a daemon handshake, successful
untrusted build, networking, application readiness or graceful shutdown.

The original control child is not assigned an artificial surviving-process
identity. Systemd may terminate it during final shutdown. Acceptance must
observe service flush and actual VMM poweroff, distinguishing the runtime's
timed host fallback from successful guest shutdown; control EOF alone is not
success. No mixed-libc shutdown compatibility is implied by this archive.

## Complete additive registration

Each base or leaf first materializes its complete `contents`, optional
`rootfs`, and `extraCommands` output, then computes that payload's closure.
Store references in OCI configuration are included through a separate root.
Immutable registration and root manifests are copied to literal paths under
`/nix/guest-registrations`; the system unit does not reference a derivation
which closes over its own toplevel. Each leaf needs a unique lowercase
`registrationName`; base registration cannot be replaced.

Leaf customizations cannot replace `/init`, runtime hosts/resolver/CA files,
`/run`, Nix state or registration metadata. This is a build-time contract for
trusted Nix expressions, not confinement against malicious build scripts.
The shared constructor does not call `includeNixDB` for each layer. The database,
new store paths and GC roots live in the guest's writable image overlay, not a
host store/socket. Persisting a database without its corresponding store paths
is invalid. No live persistent-volume migration is implemented here.

The base archive remains an exact parent layer prefix, but dockerTools can
re-emit overlapping store paths in child layers. `fromImage` is not proof of
cross-leaf byte deduplication or imported-runtime cache reuse. Measure actual
closure overlap and archive bytes before claiming those optimizations.

The exported base reserves at most 64 layers; direct leaves default to 100.
`guestLayerBudget` is a declared upper bound, not an observed layer count.
The leaf constructor requires at least two layers beyond that parent budget:
one for new payload and one for customization. Thus a 100-layer parent with
a 100-layer child is rejected during evaluation, not after expensive assembly.
Further layering must explicitly reserve additional capacity within dockerTools'
supported limit; direct leaves should normally share the original base.

## Checks and remaining gates

The ordinary `checks.x86_64-linux.nixos-image-contract` evaluates the module
contract and runs the actual registration shell against an inert command
double in temporary directories. It does not build the NixOS image or execute
Nix store operations. Run the portable shell tests directly with:

```sh
python3 tests/nixos-image/test_registration.py -v
bash -n lib/guest/register-store.sh
```

Inspect expensive work before selecting either archive:

```sh
nix build --dry-run --no-link --no-update-lock-file \
  --option allow-import-from-derivation false \
  .#packages.x86_64-linux.guest-determinate-base
```

The assembled base has 64 layers (356,108,237 compressed bytes); the hello
example has 72 (369,089,568 compressed bytes). Read-only inspection verified
every layer's SHA256 against its diffID, the unchanged parent layer prefix,
all 490 base and 493 combined registered store-path members, exact registration
and configuration-root bytes, executable generated init, regular writable CA,
and no baked database or `/run` contents. These are archive checks, not
all-file NAR verification or execution of the registration service. The
ordinary contract passed 27 assertions and 11 portable shell tests.

One actual Microsandbox activation and shutdown test remains required. It must
verify both daemon trust handshakes, an uncached untrusted declared-input
build with mapped nixbld identity, sandbox controls, runtime CA/network/control
preservation and guest-owned result persistence. Supplier-managed cache
defaults remain as documented in [Determinate guests](determinate-guests.md);
test-only no-substitution requests are not administrative network confinement.
