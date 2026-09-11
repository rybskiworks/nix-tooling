<h1 align="center">nix-tooling</h1>
<p align="center"><strong>One reviewed toolchain. Many reproducible consumers.</strong></p>
<p align="center">Shared Nix inputs, opt-in development modules, and explicit guest-building primitives.</p>
<p align="center">
  <a href="https://github.com/rybskiworks/nix-tooling/actions/workflows/ci.yml"><img alt="CI on main" src="https://github.com/rybskiworks/nix-tooling/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  &nbsp; Linux x86_64 &nbsp; | &nbsp; Nix flakes &nbsp; | &nbsp; Immutable inputs
</p>
<p align="center">
  <a href="#consume-a-reviewed-snapshot">Use it</a> &middot;
  <a href="#choose-only-what-you-need">Modules</a> &middot;
  <a href="#verification-without-shell-side-effects">Verification</a> &middot;
  <a href="CONTRIBUTING.md">Contribute</a>
</p>

---

**Stop maintaining the same compiler and formatter pins in every repository.**
`nix-tooling` owns a shared package set and Fenix toolchain, then exposes small,
opt-in modules for consumers such as Workestrate and its runtime forks.

It is a supplier, not an operator environment. Importing a module should not
initialize a tracker, install a host daemon, migrate credentials or start workloads.

```text
                    nix-tooling @ reviewed commit
                     /           |             \
              input authority  dev modules   guest primitives
                     \           |             /
                       consumer flakes follow
                    Workestrate / runtime / tools
```

## Choose only what you need

| Surface | Purpose |
| :--- | :--- |
| `devenvModules.base` | Common shell tools and formatting integration. |
| `devenvModules.nix` | Nix formatting and static checks. |
| `devenvModules.toml` | Pinned Tombi and shared TOML policy. |
| `devenvModules.rust` | Supplier-owned Fenix compiler and Rust formatting. |
| `devenvModules.beads` | Pinned CLI, without tracker initialization or synchronization. |
| `devenvModules.determinate` | Opt-in Nix client, without replacing the host daemon. |
| `lib.guest` / `nixosModules` | Explicit guest construction and optional NixOS profiles. |

The exported system is **`x86_64-linux`**. Pinned binary packages currently
constrain portability; adding a platform requires package and runtime evidence,
not just extending the flake's `systems` list.

## Consume a reviewed snapshot

This example uses an immutable, already-landed snapshot. Advance it deliberately,
then regenerate and review the consumer lockfile:

```nix
inputs = {
  tooling.url = "github:rybskiworks/nix-tooling/1120aa22cddf4a9a3424f38aadbebadd8a963c4b";
  nixpkgs.follows = "tooling/nixpkgs";
  fenix.follows = "tooling/fenix";
  flake-parts.follows = "tooling/flake-parts";
  devenv.follows = "tooling/devenv";
  treefmt-nix.follows = "tooling/treefmt-nix";
  git-hooks.follows = "tooling/git-hooks";
};
```

**One direction only.** Do not also make `tooling.inputs.nixpkgs` follow the
consumer's nixpkgs; that reverses ownership and can create a cycle. When a consumer
imports another consumer, make that dependency's tooling follow the root tooling
input. Do not independently override the supplier's Fenix revision.

For a flake-parts/devenv consumer, import only required modules and supply the
pinned Fenix overlay to the package set:

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

Rust builders and standalone formatters must use the same Fenix compiler/rustfmt.
The [Rust module](devenvModules/rust.nix) requires the expected overlay; Rust commit
hooks remain opt-in. A sibling checkout is useful with an explicit local input
override, but must not become a machine-local path or mutable ref in a published
consumer lockfile.

## Verification without shell side effects

Run offline contracts first:

```sh
python3 -m unittest discover -s scripts/ci -p 'test_*.py' -v
python3 scripts/ci/toolchain.py check --role supplier
python3 scripts/ci/check_repository.py
```

With Nix available, evaluate or build **ordinary checks only**:

```sh
python3 scripts/ci/run_checks.py evaluate
python3 scripts/ci/run_checks.py full
```

The runner enumerates `checks.x86_64-linux`, validates derivation outputs and builds
that exact set. It does not evaluate the interactive shell, install hooks, rewrite
the lockfile or run optional KVM tests. This avoids the documented development-root
requirement that made the previous bare whole-flake CI invocation inconsistent
with local development. It is not a claim that every output was validated.

For an interactive shell, use the explicit-root wrapper:

```sh
./scripts/devenv-shell.sh
```

Whole-flake/development-shell validation is separate and requires an explicit root
input. See [CI and releases](docs/ci-releases.md); never commit a temporary local
root path in flake.lock.

Hook installation is **disabled by default** by the base module. Installing hooks
is a separate operation; generated store-path-bearing hook configuration is not
source. Formatter policy lives in `share/tombi-format.toml`; ordinary checks reject
drift from the shared policy.

## Guest and service building blocks

Guest assembly, boot, daemon activation and protocol compatibility are separate
contracts. Optional packages do not implicitly become ordinary CI gates.

| Guide | Boundary |
| :--- | :--- |
| [Guest images](docs/guest-images.md) | Runtime-neutral construction primitives. |
| [Determinate guests](docs/determinate-guests.md) | Explicit client/Nixd profile and optional VM tests. |
| [NixOS OCI images](docs/nixos-oci-images.md) | Base/leaf assembly, registration and activation limits. |
| [Beads SQL service](docs/beads-server.md) | External server configuration and native compatibility evidence. |

Installing Beads is not tracker migration. Before changing an existing tracker,
stop writers, back up its complete state and rehearse on a disposable copy. Do not
initialize or synchronize databases from shell-entry hooks.

## Maintenance

`main` is the integration branch. Input ownership stays here; consumer pins advance
through reviewed PRs. [`version.txt`](version.txt) is source-version metadata, not
proof that a release exists or all runtime tests passed.

Read [contributing](CONTRIBUTING.md), [security reporting](SECURITY.md),
[GitHub governance](docs/github-governance.md) and the
[repository audit](docs/repository-audit-2026-09-11.md). The audit records the
outstanding project-license decision rather than assuming a license on the owner's
behalf.
