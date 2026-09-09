# Shared guest images

`lib.guest.mkImage` is a thin, runtime-neutral wrapper around the pinned
`dockerTools.buildLayeredImage`. It requires an explicit package set and adds no
runtime, Nix engine, daemon, Beads, agent client, boot shim or guest registration.
The default tooling package and development shells remain unchanged.

```nix
let
  pkgs = import inputs.tooling.inputs.nixpkgs { system = "x86_64-linux"; };
in
inputs.tooling.lib.guest.mkImage {
  inherit pkgs;
  name = "example-service";
  tag = "v1";
  contents = [ pkgs.busybox pkgs.dockerTools.fakeNss ];
  config = {
    Cmd = [ "/bin/sh" ];
    WorkingDir = "/tmp";
    Env = [ "PATH=/bin" ];
  };
  extraCommands = ''
    mkdir -p tmp
    chmod 1777 tmp
  '';
}
```

The result is the original archive derivation, including dockerTools' `.stream`
passthrough. `tag = null` retains its output-hash tag default. Creation time stays
deterministic. `contents` is a list of derivations or Nix paths; optional `rootfs`
is one more filesystem tree merged into those contents. Use `extraCommands` for
permissions and empty directories: source directory permissions do not imply
the merged image permissions. These commands run at image build time, not in a
guest. No package downloads belong in them.

`fromImage` accepts a parent archive derivation or Nix path. `maxLayers` retains
dockerTools' default of 100 and must be greater than one; callers own any tighter
runtime limit. Parent layers count toward the layer budget. Configuration and
layer handling remain dockerTools semantics, not a new merge or deployment
language. Keep image names, tags and contents explicit. This interface does not
promise runtime disk deduplication merely because a parent archive is shared.

For uncommon dockerTools options, call dockerTools directly instead of assuming
the wrapper accepts arbitrary arguments. Database initialization, union
registrations, signing, publishing and runtime launch are separate operations.
In particular, this constructor does not install per-layer SQLite databases or
silently enable `includeNixDB`.

## Optional NixOS-derived configuration

`nixosModules.guestBase` contains opt-in container/minimal defaults, with Nix
disabled until a consumer explicitly selects its engine and daemon policy. It
does not choose a hostname, users, credentials, state version or init executable.

`lib.guest.mkNixosSystem` evaluates that module with the supplied package set:

```nix
inputs.tooling.lib.guest.mkNixosSystem {
  inherit pkgs;
  stateVersion = "26.05"; # Preserve the existing system's compatibility version.
  modules = [
    { networking.hostName = "example-guest"; }
  ];
}
```

This returns the ordinary `nixosSystem` result, not an image. The module evaluator
comes from tooling's pinned nixpkgs; `nixpkgs.pkgs` uses the caller's exact package
set, including overlays and configuration. Consumers should normally follow
`tooling/nixpkgs`; do not independently reimport another package set inside the
guest modules. Keep state-version changes deliberate.

Creating a toplevel or baking a profile symlink does **not** activate NixOS.
Actual init handoff, `/etc` and users, daemon readiness, writable store state,
build sandbox capabilities and shutdown require separate runtime tests. A
minimal shell image is neither a NixOS system nor a native Nix builder. No
Determinate engine or Nixd selection is made by this library.

## Tests

The cheap contract is part of the ordinary tooling checks. It compares native
dockerTools archive/stream identities, rejects invalid argument shapes, and
verifies package-set injection and the absence of an implicit engine selection:

```sh
nix build .#checks.x86_64-linux.guest-contract
python3 -m unittest discover -s tests/guest -p 'test_*.py' -v
```

Image realization is opt-in, so formatter/devshell consumers do not evaluate or
build a NixOS toplevel or guest image:

```sh
nix build --dry-run .#guest-minimal \
  .#legacyPackages.x86_64-linux.guestChecks.archive \
  .#legacyPackages.x86_64-linux.guestChecks.closure
nix build --no-link --max-jobs 1 --cores 2 \
  .#legacyPackages.x86_64-linux.guestChecks.archive \
  .#legacyPackages.x86_64-linux.guestChecks.closure
```

The archive check reads tar members without extracting or executing them. It
checks deterministic metadata, shell/NSS link targets, a sticky `/tmp`, runtime
content-closure paths, and the absence of implicit Nix database/profile state or
engine/runtime/client dependencies. A flattened `fakeNss` aggregation root need
not be present, but its real content roots must be; dangling aggregate links or
configuration references are rejected. The closure check independently inspects
the full content closure, including that aggregation root. The archive check
retains `report.json` with its archive digest, byte size and checked counts.
These are constructor checks, not VM, systemd, network, daemon,
nested-virtualization or application tests.
