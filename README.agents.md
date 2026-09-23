# Consuming nix-tooling

Use this guide when integrating `nix-tooling` into **another rybskiworks repository**.
It describes the consumer contract, not instructions for editing this repository.
Follow the target repo's `AGENTS.md` and contribution rules for edits; for changes
here, read [CONTRIBUTING.md](CONTRIBUTING.md). The [human overview](README.md)
explains the purpose and ownership split.

## Pin and follow the shared inputs

`nix-tooling` owns shared tooling revisions. Consumers select a reviewed supplier
commit and commit their own Nix-generated `flake.lock`. The example below is a
concrete landed snapshot for shared input wiring, **not an instruction to upgrade
an existing consumer**. Check that the selected revision exports each required
output, and only declare inputs the consumer actually uses.

```nix
inputs = {
  tooling.url = "github:rybskiworks/nix-tooling/8ee08bfdce0b871120f933c85005a96c08846f59";
  nixpkgs.follows = "tooling/nixpkgs";
  fenix.follows = "tooling/fenix";
  flake-parts.follows = "tooling/flake-parts";
  devenv.follows = "tooling/devenv";
  treefmt-nix.follows = "tooling/treefmt-nix";
  git-hooks.follows = "tooling/git-hooks";
};
```

**Ownership flows one way.** Do not also set
`tooling.inputs.nixpkgs.follows = "nixpkgs"`, creating a cycle, or independently
override the supplier's Fenix revision. Change shared pins here, then promote the
reviewed change through consumer PRs. Keep project-specific dependencies and policy
in the consumer; nix-tooling must not depend back on Workestrate.

When composing compatible internal consumers, align their tooling input with the
root's deliberately. Workestrate does this for its runtime fork:

```nix
inputs.microsandbox-fork.inputs.tooling.follows = "tooling";
```

This overrides that dependency's standalone tooling choice, so test the combined
graph. Do not flatten every third-party input blindly: Determinate's package set
here and `llm-agents` in the fleet deliberately retain upstream dependency graphs
to preserve their prebuilt artifact identities.

## Choose the smallest surface

Outputs are defined in [`flake.nix`](flake.nix). Read the version at the consumer's
locked commit, not just `main`; older consumers need not expose every output below.
The exported system is `x86_64-linux`.

| Need | Consume |
| :--- | :--- |
| A packaged tool | `tooling.packages.${system}.{tombi,beads,lix,determinate-nix,determinate-nixd,dolt-bin,dolt-secure-transport}`. Select a named output, not an assumed default. |
| A development environment | `tooling.devenvModules.{base,nix,toml,rust,beads,lix,determinate}`. Import only what the project needs. |
| Image assembly | `tooling.lib.guest.{mkImage,mkNixosSystem,mkNixosImage,mkNixosLayer}` with explicit consumer arguments. |
| A shared parent image | `tooling.packages.${system}.{guest-lix-base,guest-determinate-base}`. Reuse the selected parent when composing leaves. |
| Guest profiles | `tooling.nixosModules.{guestBase,microsandboxGuest,lixGuest,determinateGuest,devenvCache}`. See the engine/image guides before activation. |
| A signed-cache reader | `tooling.nixosModules.cacheClient`, or `import tooling.lib.cacheClient { ... }` for a settings fragment. |
| Human co-author attribution | `tooling.packages.${system}.git-attribution` and `tooling.nixosModules.gitAttribution`; [strict identity admission, hooks and squash workflow](docs/git-attribution.md). |

Here `tooling` means `inputs.tooling`; brace groups in the table abbreviate separate
attributes, not Nix expressions. Prefer exported packages/helpers over copying
packaging files or importing private implementation paths.

For a flake-parts/devenv consumer, import `inputs.devenv.flakeModule` at the
flake-parts level. This fragment belongs **inside its `perSystem` module**, where
`system` and `inputs` are in scope:

```nix
_module.args.pkgs = import inputs.nixpkgs {
  inherit system;
  overlays = [ inputs.fenix.overlays.default ];
};
devenv.shells.default.imports = [
  inputs.tooling.devenvModules.base
  inputs.tooling.devenvModules.nix
  inputs.tooling.devenvModules.toml
  inputs.tooling.devenvModules.rust
];
```

The Rust module requires that pinned Fenix overlay. Use the same
`inputs.fenix.packages.${system}.stable` toolchain for Rust builders and standalone
rustfmt; do not introduce a second toolchain via devenv's `languages.rust`.
Rust commit hooks are opt-in. Config-only development shells can omit Rust.

Importing devenv modules does **not** export the consumer's `formatter` or flake
`checks`. Wire its flake-level treefmt/git-hooks modules or existing project checks
separately, keeping the tools consistent. TOML uses a custom Tombi formatter/hook
at these pins; shared formatting policy is in
[`share/tombi-format.toml`](share/tombi-format.toml), while schemas and file scope
belong to the consumer.

## Preserve reuse without widening trust

Shared revisions reduce drift; reuse still depends on the actual derivations.
Prefer the supplier's existing package outputs and shared guest parents. For a
cache miss, compare the selected system, input graph, overlays/options and source
filters before assuming the cache is broken. Matching compiler version strings
alone do not imply matching store paths.

`nixosModules.cacheClient` adds named `nix.cacheClients.<name>` entries, each with
one `endpoint` and one `publicKey`. `lib.cacheClient` is a **path to a function**:
import it with `lib`, `endpoint` and `publicKey`. Both support an explicit
loopback-only `insecureLocalEndpoint` exception. Use the
[cache-client guide](docs/cache-clients.md) for the exact API and rejection rules;
`nixosModules.devenvCache` is the separate opt-in pinned public-cache profile.

A cache declaration neither publishes outputs nor grants network access. Keep
reader credentials, signing keys and upload tokens out of source, flake URLs,
lockfiles, store paths and images. These helpers configure verification, not a
publisher or remote builder; do not disable signature checking or sandboxing to
make substitution work. Inspect the effective configuration and test substitution
separately from module evaluation.

Image-layer reuse, binary-cache substitution and a shared guest `/nix/store` are
separate mechanisms. Do not turn the shared-store or per-fleet builder proposals
([#15](https://github.com/rybskiworks/nix-tooling/issues/15),
[#19](https://github.com/rybskiworks/nix-tooling/issues/19)) into assumed APIs.
Do not mount a shared writable store or host daemon socket into agent workloads.

## Update and validate in the consumer

Change the intended `tooling.url` commit, then regenerate only the relevant lock
graph with Nix and inspect it:

```sh
nix flake update tooling
git diff -- flake.nix flake.lock
```

Never hand-edit integrity hashes or perform an unrelated blanket input update.
Validate the consumer's affected packages/checks and its own formatting/tests,
including dependencies whose tooling selection changed through `follows`.
Use the consumer's documented entry points and `--no-update-lock-file` for strict
locked validation; `--no-write-lock-file` alone only prevents writing the lock.
Supplier CI passing does not prove downstream compatibility,
image boot or runtime isolation. [`version.txt`](version.txt) is metadata, not
proof of a published release or successful adoption.

For local iteration, override the input explicitly, for example
`--override-input tooling git+file:///absolute/path/to/nix-tooling`, with
`--no-write-lock-file`. A Git-filtered checkout includes tracked changes, not
untracked files. A `path:../nix-tooling` override is also possible, but can include
ignored build artifacts. Neither local paths nor temporary overrides belong in
the committed consumer pin or lockfile.

The pinned devenv flake integration needs an explicit development root. Prefer the
consumer's root-aware shell wrapper. For consumers using the `devenv-root`
placeholder, the override is a **regular file containing the worktree's absolute
path**, not the directory itself. Do not point shell state at the read-only source
in `/nix/store`. See [the wrapper here](scripts/devenv-shell.sh) and
[validation boundaries](docs/ci-releases.md#separate-whole-flakedevelopment-shell-validation).
Ordinary-check evaluation and whole-flake/development-shell evaluation are distinct.

Shell entry skips the shared lint/format tasks, but pinned devenv can still install
its hook shim in `.git/hooks`. Tests/formatters may modify the worktree; use the
appropriate fail-on-change checks in CI. Adding a CLI is not permission to replace
a host daemon, initialize/synchronize a tracker or migrate a live database.
Image assembly likewise does not prove guest activation; preserve the consumer's
explicit `stateVersion` and run the relevant engine/runtime gates.

## Follow real consumers, not historical pin tables

| Reference | Pattern to inspect |
| :--- | :--- |
| [Workestrate flake](https://github.com/rybskiworks/workestrate/blob/main/flake.nix) | Shared input follows, Fenix alignment and consumption of the runtime fork's packaged outputs. |
| [Microsandbox flake](https://github.com/rybskiworks/microsandbox/blob/main/flake.nix) | Independently pinned runtime packaging; nested tooling alignment for libkrunfw. |
| [Fleet flake](https://github.com/rybskiworks/workestrate-fleet/blob/main/flake.nix) | A shared guest parent, supplier packages and consumer-owned workload image composition. |
| [Operator config](https://github.com/rybskiworks/workestrate-config-georgrybski/blob/main/README.md) | Registry/configuration ownership, not a template for a direct tooling flake integration. |

These are navigation links, not dependencies on `main`. Inspect each actual lock
and source revision; do not assume all consumers currently share one tooling SHA.
For guest/service details, use the [focused guides](README.md#find-the-right-guide)
rather than duplicating their contracts here.
