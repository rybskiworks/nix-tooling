# NixOS OCI images with a guest-owned Nix store

The experimental `packages.x86_64-linux.guest-determinate-base` output is one
shared NixOS userspace archive, with the supported Determinate Nix/Nixd pair.
The exported common base explicitly imports `nixosModules.devenvCache`, adding
the public devenv Cachix endpoint and exact signing key from the locked supplier.
All leaves inherit this one configuration; no leaf should replace `/etc/nix`.
The cache module remains opt-in for independently constructed NixOS systems.
It does not add a trusted user, relax signatures or sandboxing, grant network
access, or install credentials. Nixd's managed supplier defaults remain intact.
It does not build a kernel, boot a VM, activate a host service, or initialize
Beads. The runtime supplies the kernel and must explicitly invoke the generated
NixOS init. Base and example-leaf assembly and read-only archive checks have
passed on x86_64-linux. The separate opt-in Microsandbox test has also passed
fresh-guest activation, untrusted builds, two-boot persistence and normal
poweroff. Application leaves and historical runtime state require their own
acceptance tests; the standard NixOS VM test is a separate result.

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

The Microsandbox profile omits only NixOS's `kernel.pid_max=4194304` tuning:
the supplied firmware can reject that value even on a 64-bit guest. It keeps
the kernel's own supported default without guessing its maximum. An explicit
consumer `boot.kernel.sysctl."kernel.pid_max"` value can override this choice;
all other sysctl settings and failed-unit checks remain enabled. The guest
probe records the observed `/proc/sys/kernel/pid_max` on each boot.

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

The base archive remains an exact parent layer prefix. The leaf constructor
uses dockerTools' build-time layering pipeline to exclude the union of actual
ancestor store-layer paths before forming new layers. `guestStoreLayerConfigs`
retains the supplier inventories across generations. This changes only archive
layer selection: each leaf's complete registration closure and roots remain
intact. It does not imply deduplication between independent sibling archives or
cache reuse after runtime import.

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

The cache-enabled assembled base has 64 layers (356,108,629 compressed bytes).
The registered hello child has 68 layers (356,171,991 compressed bytes), and its
grandchild has 70 (356,177,370 compressed bytes). The explicit
[archive inheritance check](../tests/nixos-image/README.md) passed on all three:
every layer's SHA256 matches its diffID, each inherited prefix remains exact,
and newly emitted store paths exclude every ancestor's inventory while retaining
new payload and configuration roots. The respective complete image inventories
contain 490, 493 and 495 store paths; independently computed registration data
and every ancestor's registration remain unchanged.

Separate base inspection verified executable generated init, a regular writable
CA bundle, and no baked database or `/run` contents. These are archive checks,
not all-file NAR verification or execution of the registration service. The
ordinary contract passed its evaluated assertions and 122 portable tests:
11 registration, 14 layer-pipeline, 24 archive-inheritance, eight supervisor,
54 smoke-oracle and 11 shutdown-diagnostic tests. The three-archive check is
opt-in and is not realized by the ordinary contract.

### Opt-in Microsandbox smoke

`tests/nixos-image/msb_smoke.py` consumes an already-built archive and runtime;
it never builds or downloads them. The runtime is an explicit argument, avoiding
a tooling-to-runtime flake input cycle. Its installed CLI and bundled agentd,
and the archive, must match caller-supplied SHA256 values. The reported source
revision is a caller assertion, not something inferred from executable bytes.
`legacyPackages.x86_64-linux.nixosImages.smokeSpec` contains the exact base
and probe-tool paths without making their realization a dependency of the
small metadata output. Realize and review those inputs separately first.

The runner requires Linux pidfds/subreaping, accessible KVM, 22 GiB free disk
and 8 GiB available host memory. It creates one private scratch root, empty
MSB configuration, isolated HOME/XDG directories, no inherited credentials,
and a unique local image tag. It uses `--pull never`, `--no-net`, no host
mounts or exposed ports, one CPU, 2 GiB guest memory and a 4 GiB managed disk.
The pinned runtime adds its exact guest-only 512 MiB `/tmp` tmpfs. The inspect
oracle accepts only that default mount and the explicit disabled TLS/default
empty-secret objects; it rejects extra fields, mounts or network capabilities.
Guest root retains the default unrestricted VM policy. The untrusted Nix
client is a disposable `nix-smoke` account (UID 1000, primary group `users`,
GID 100), created with the image's ordinary `useradd` after checking the name,
UID and private home are absent and the existing group is exact. It has no
login shell or supplied password; actual NSS/process identity checks reject
extra groups. The daemon's `root @users` admission and root-only trust policy
are not changed. Existing `nobody:nogroup` must remain denied; it is not an
admitted untrusted client. A second boot must retain the exact recorded test
account before recreating its volatile private home.

Root and admitted-client trust handshakes run before the nobody negative. The
negative makes at most three identical requests, retrying only the exact
16-byte greeting broken-pipe response with the expected otherwise-empty daemon
JSON. Every attempt is retained, and at least one explicit authorization denial
is mandatory: success, other errors or three transport failures still fail.
The [client handshake](https://github.com/DeterminateSystems/nix-src/blob/3ed5caa3bbde51ab87977c8a80e27c6ed0ea8534/src/libstore/worker-protocol-connection.cc#L158-L168)
flushes its greeting before reading the denial;
[the daemon](https://github.com/DeterminateSystems/nix-src/blob/3ed5caa3bbde51ab87977c8a80e27c6ed0ea8534/src/nix/unix/daemon.cc#L337-L350)
can reject a peer before reading that greeting. A broken pipe alone is not authorization evidence.
Failure captures bounded read-only daemon unit, journal and effective-config
diagnostics without replacing the original failure or changing daemon policy.

There is no additional host network namespace: egress denial is the runtime's
guest network policy, while the host CLI has no registry pull permission or
external endpoint in this fixture. This is not a malicious-runtime boundary.
The verified gzip archive is streamed into a fresh owned plain TAR for MSB's
importer, with a 2 GiB decompressed limit inside the same scratch budget;
its byte count and SHA256 are recorded. This step does not extract members
onto the host or rebuild/change the image.
Before every guest command, the runner requires Running state and exactly one
owned VMM, pins that process with a duplicated pidfd, and checks its continued
identity afterward. The CLI itself can auto-start stopped sandboxes and has
no atomic no-start option: the check-to-call race is not eliminated, but an
observed replacement or original-process death cannot pass this fixture.

Run `python3 -B tests/nixos-image/msb_smoke.py --help` for the required explicit
input/hash arguments. `--execute --mode activation` only checks PID 1,
volatile `/run`, control, registration, runtime CA bytes, daemon pair and
root-versus-untrusted handshakes. It reports `activation_only_passed`, never
full acceptance. `--execute --mode full` additionally requires two genuinely
uncached guest builds with exactly three declared inputs, outside-file and
loopback-listener positive controls, mapped nixbld identity, rejected client
security overrides, daemon restart, persistent output/GC roots across a VM
stop/start, and volatile `/run` replacement. No provider or external cache
success is tested; builds request `substitute=false` and retain managed cache
defaults. Both boots must retain the exact signed devenv cache/key addition;
the probe rejects adding that endpoint to `trusted-substituters`. Other
Nixd-managed cache defaults are recorded, not certified as an exclusive list.
Both boot-unit snapshots must also report no failed units for the final full
verdict. Failed or unavailable unit-health diagnostics do not prevent the
independent build/persistence/shutdown checks from executing, but those
successful subresults cannot make an unhealthy guest pass full acceptance.

Full-mode shutdown requires a nonce-bearing system-console flush marker,
successful complete log retrieval, an observed normal exit of the exact owned VMM, and completion before the
runtime's eight-second host-fallback window, with no observed escalation.
CLI success or control EOF alone cannot pass. Each boot gets its own marker;
the first must also persist across restart. Activation mode does not establish
this shutdown or persistence contract.
The original VMM is pinned before the stop command. The runner allows bounded
read-only unit/process snapshots before each normal stop, using the same
metadata allowlist as the guest-requested diagnostic below. This adds evidence,
not a sleep or a changed stop limit. The runner then allows bounded
passive observation after the command returns; elapsed time includes that
observation and must still satisfy the strict eight-second acceptance limit.
Missing or ambiguous exit evidence fails, and captured shutdown logs remain in
the partial stop receipt even when exit observation fails.
An explicit kernel report that poweroff is unavailable and the system halted
instead is a failure even if a normal VMM exit was observed.

`--execute --mode shutdown-diagnostic` runs the same first-boot controls,
uncached builds and daemon restart, then requests `poweroff.target` directly
through the guest's systemctl. It does not send the host stop request or
override its timeout. Before the request it records the effective daemon and
manager stop settings, active units, and bounded process identity/state/signal
mask/cgroup metadata. Only argv[0] is read; environments and other arguments
are excluded. The original VMM is then observed passively for up to 30 seconds.
The additional observation can expose systemd's final remaining-process
diagnostics, which need not appear within the host's shorter grace period.
The command's precheck is not atomic with CLI exec; an observed replacement
fails, and the observer never issues a start request.

This mode reports `guest_poweroff_diagnostic_passed` only with acknowledged
guest request, normal original-VMM exit, fsynced marker, kernel power-down
sequence, successful complete log capture and no observed fallback or signals.
It never sets full acceptance, does not restart the guest, and cannot replace
normal host-stop timing or persistent-store validation. Its result includes
the actual elapsed time and whether that time fits the ordinary host grace.
A timeout or ambiguous exit remains a failed diagnostic with bounded logs and
the same owned cleanup, not an inferred poweroff.

Work has a 300-second deadline, then at most 90 seconds of owned cleanup.
Continuing floors are 17 GiB disk and 6 GiB available host memory. The runner
samples its own allocated scratch size every five seconds, capped at 4 GiB,
and also stops on more than 4 GiB of global free-space decrease from admission.
The latter includes unrelated allocation and is checked with the frequent
free-space samples. At minimum admission that allowance leaves 18 GiB, one
GiB above the unchanged free-space floor. These are sampled stop limits, not
kernel-enforced quotas or guarantees against growth between samples. The
runner retains limits, observed allocation peaks, bounded logs and `result.json`.
It snapshots diagnostics before
forced cleanup on failures, removes only its sandbox, and requires an empty
owned backend and no remaining child processes. Forced cleanup cannot pass.
Owned executable identities are captured before cleanup and after stop, with
signal recipients and observed exit events retained. A separate post-stop
log snapshot precedes removal. These records distinguish a stopped VMM from
remaining helpers without reading host process arguments or environments;
they do not assume every signal caused a process to exit.
The scratch directory is retained for inspection; no global cleanup is used.

Ordinary checks execute only the portable supervisor and receipt tests:

```sh
python3 -B -m unittest discover -s tests/support -v
python3 -B -m unittest discover -s tests/nixos-image -p test_smoke_contract.py -v
python3 -B -m unittest discover -s tests/nixos-image -p test_shutdown_diagnostic.py -v
```

These tests exercise detached-child ownership and negative receipt predicates,
not a simulated enforcement result. `fixtures/msb-inspect.json` preserves an
actual isolated-runtime response from Microsandbox `53ec614`, with only names
and its local image reference normalized and incidental timestamps omitted.
It is independent of the expected-value predicate.

An initial probe reached systemd and the selected registration/socket/control
checks, then failed because its inspect predicate incorrectly rejected the
automatic guest tmpfs. That failure is not an activation pass. The console
also reported a failed `systemd-sysctl.service`; selected-unit readiness does
not mean all units are healthy. The runner now retains a bounded failed-unit
listing, sysctl unit result/exit/output metadata, read-only sysctl configuration,
and the last 100 sysctl journal entries with and without a boot filter before
evaluating inspect. Identifier-filtered sysctl entries and warning-level
journal entries without a unit filter cover missing unit attribution.
If a full run fails while its original VMM is still Running, a separate
five-second diagnostic intentionally reapplies the exact default sysctl
configuration with debug output to the console. Its bounded output and exit
status are retained without changing the primary failure or boot health.
It never starts a stopped guest, replays against a replacement VMM, or
upgrades a failed result; ordinary boot diagnostics remain read-only.
Those diagnostics do not suppress failures or claim system-wide health.
A subsequent probe passed the corrected inspect, exact registration of 490
store paths/roots, unchanged runtime CA and the trusted root handshake, then
correctly denied `nobody` at the daemon's admission boundary. That failed
receipt is not full activation success. The ordinary-user correction was then
exercised: registration, root trust, ordinary untrusted access and nobody
denial passed. Its overall result still failed because owned cleanup recorded
a signal; the earlier receipt did not identify the signal recipient. The
sysctl unit still reported exit 1 without explanatory unit journal entries.
Neither failure was waived; those attempts did not establish uncached builds,
persistence or graceful shutdown.
The retained console records denied supplier DNS attempts; this is not a
claim that no traffic was attempted.

A fresh Linux x86_64 full run subsequently passed with systemd260.1, the matched
Determinate Nix/Nixd3.22.3 pair, and source-built libkrunfw5.6.1 using Linux6.12.108.
It exercised both actual uncached builds with namespace UID1000 mapped to guest
nixbld UID30001, rejected untrusted security overrides, daemon restart, two
healthy boots, verified persistent outputs and GC roots, a persistent fsynced
marker, and replacement of volatile `/run`. Both normal host-requested stops
exited the original VMM without fallback or supervisor signals in4.412s and
0.415s; total fixture runtime was62.247s. Cleanup left no sandbox or owned child.
The observed kernel pid_max was4096 on each boot; no unsupported numeric tuning
or ignored failed unit was required.

A separate direct guest-requested diagnostic also reached actual kernel
poweroff in4.283s. An earlier host-triggered failure was not reproduced in the
controlled run with the same bounded pre-stop snapshots. That comparison does
not establish the earlier failure's cause or guarantee a general shutdown
latency; the normal acceptance still rejects the unchanged eight-second
fallback boundary. Historical failed results remain distinct from this pass.

This is fresh base-image validation, not acceptance of every workload leaf,
old snapshots, live-state migration, nested virtualization or another platform.
Supplier-managed cache defaults remain as documented in
[Determinate guests](determinate-guests.md); no external cache success was
tested, and per-request no-substitution is not administrative confinement.
