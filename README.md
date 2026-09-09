# nix-tooling

Shared Nix dev tooling for the `rybskiworks` flakes: formatter, git-hooks, Tombi, Rust via fenix, and Beads.

This flake is designed as a **sibling** to `workestrate` (`path:../nix-tooling` locally, `github:rybskiworks/nix-tooling` when published) and exposes reusable `devenvModules` so consumers can opt-in without pulling in unwanted tooling.

## Architecture

```
nix-tooling/
├── flake.nix                 # flake-parts + devenv + treefmt-nix + git-hooks + fenix
├── packages/tombi.nix        # pinned Tombi (version: share/tombi-version; sha in packages/tombi.nix)
├── devenvModules/
│   ├── base.nix              # treefmt + typos + shell fundamentals
│   ├── nix.nix               # nixfmt + statix (+ deadnix)
│   ├── toml.nix              # Tombi handling (custom formatter + lint hook)
│   ├── rust.nix              # fenix stable toolchain + rustfmt/clippy (hooks opt-in, mkDefault false)
│   └── beads.nix             # pinned Beads CLI only; no database or hook initialization
├── tombi.toml
└── README.md
```

### Inputs and version pins

| Input | Rev | Follows | Notes |
|-------|-----|---------|-------|
| `nixpkgs` | `a799d3e3886da994fa307f817a6bc705ae538eeb` (nixos-unstable) | — | Consumers share this package set with `nixpkgs.follows = "tooling/nixpkgs"`. |
| `flake-parts` | `9d0d87172c374f89da73c1cfe6d81ae62feac1f1` | `nixpkgs` | |
| `devenv` | `97135e80b6e432f41f84f72383e1b8147f33ef0c` | `nixpkgs`, `git-hooks`, `flake-parts` | |
| `treefmt-nix` | `27b3b12a8e6375f28ebe122f07d230ca5459bbfa` | `nixpkgs` | No `programs.tombi` (128 programs checked). |
| `git-hooks.nix` | `27555e2624241fb116b49095df4caaee85a25691` | `nixpkgs` | No `hooks.tombi`; `hooks.treefmt.settings.fail-on-change` defaults `true`. |
| `fenix` | `fa09e6473a0dfd673e6cb9a37741aec513b4bb2a` (rustc 1.97.1) | `nixpkgs` (tooling) | **Owned pin** — consumer must NOT `follows`-override `tooling.inputs.fenix` so the toolchain stays reproducible. |
| `beads` | `6c124203e771433a3550c348771a5b5e27fd3c21` (1.2.2) | `nixpkgs` | Upstream's embedded-capable Go package, built with the shared package set. |
| `determinate` | `cb76ac22754f6b36c008a3c39477c174a146dd6b` (3.22.3) | Supplier locks preserved | Opt-in supported guest engine/Nixd; preserves upstream binary-cache identities. |

*Follows discipline*: consumers follow the shared inputs owned here, including the curated Fenix revision. Remove inverse `tooling.inputs.nixpkgs.follows = "nixpkgs"` overrides when following `tooling/nixpkgs`, since combining both directions creates a cycle. When composing consumers, make their `tooling` inputs follow the root's `tooling` input.

### Tombi handling (why custom)

`treefmt-nix` and `git-hooks.nix` have no native Tombi support. We vendor a custom formatter:

```nix
treefmt.config.settings.formatter.tombi = {
  command = "${tombi}/bin/tombi";
  options = ["format"];
  includes = ["*.toml"];
};
```

and a custom git hook:

```nix
git-hooks.hooks.tombi-lint = {
  enable = true;
  entry = "${tombi}/bin/tombi lint --error-on-warnings";
  files = "\\.toml$";
  pass_filenames = false;
};
```

Formatting is driven by `treefmt` (so `nix fmt` formats `*.toml` via `tombi format` alongside `*.nix`/`*.rs`). Lint is a separate hook.

**Auto-fix on commit**: `git-hooks.hooks.treefmt.settings.fail-on-change`:

- `false` → `git commit` auto-formats staged files (including `*.toml` via Tombi) and succeeds. This is the `devenvModules/toml.nix` default (developer-friendly).
- `true` (default for `git-hooks.nix`) → commit fails when formatting is needed; run `nix fmt` manually. This is the `perSystem.pre-commit` (CI) setting in `nix-tooling`'s own `nix flake check`.

Override per repo:

```nix
git-hooks.hooks.treefmt.settings.fail-on-change = lib.mkForce true;  # CI fail-closed
```

### Canonical rules, drift gate, and PATH-safe wrapper

- **Canonical source**: `share/tombi-format.toml` owns the single source of truth for `toml-version` + `[format.rules]` (`indent-width = 2`, `line-width = 100`) and `[lint.rules]` (`dotted-keys-out-of-order = "warn"`, `tables-out-of-order = "warn"`). Header: `canonical format/lint rules — vendored into per-repo tombi.toml; drift checked by scripts/check-tombi-sync.sh`.
- **Per-repo `tombi.toml`**: vendored copy of the canonical block plus per-repo `[files]` `include`/`exclude` and `[[schemas]]`/`[schema]` scoping. Do not edit the vendored block directly — edit `share/tombi-format.toml` and re-vendor.
- **Drift gate**: `scripts/check-tombi-sync.sh` extracts and compares normalized `toml-version` + `[format.rules]` + `[lint.rules]` (sorted, whitespace-normalized, ignoring section order and `[files]`/`[[schemas]]`) against `share/tombi-format.toml`. Fails with `diff -u` on drift.
  ```sh
  ./scripts/check-tombi-sync.sh ./tombi.toml
  ./scripts/check-tombi-sync.sh ./tombi.toml ../workestrate/tombi.toml
  # from workestrate repo root:
  # ../nix-tooling/scripts/check-tombi-sync.sh ./tombi.toml
  ```
- **PATH-safe wrapper**: `packages/tombi.nix` installs the real binary as `$out/bin/.tombi-wrapped` and a wrapper at `$out/bin/tombi` that walks up from `$PWD` to `git rev-parse --show-toplevel` (fallback `/`) looking for `tombi.toml`, `.tombi.toml`, `tombi/config.toml`, or `pyproject.toml` containing `[tool.tombi]`. If found, `exec`s the wrapped binary with `TOMBI_OFFLINE="${TOMBI_OFFLINE:-true}"`; if not, exits 1 with `tombi: no tombi.toml in scope (walked up to <root>) — use 'nix fmt'/'treefmt' or add tombi.toml; this repo may use its own formatter (e.g. taplo)`.

## Usage

For explicit, runtime-neutral image assembly and an optional NixOS-derived
configuration, see [Shared guest images](docs/guest-images.md). The guest library
does not change the default tooling package or choose a Nix engine/daemon.
The separate [supported Determinate guest profile](docs/determinate-guests.md)
selects the official engine and Nixd only when imported; its NixOS VM test is
explicitly opt-in and does not run with ordinary tooling checks.

Add as a flake input:

```nix
{
  inputs.tooling.url = "path:../nix-tooling"; # locally
  # inputs.tooling.url = "github:rybskiworks/nix-tooling"; # after publish
  inputs.nixpkgs.follows = "tooling/nixpkgs";
  inputs.fenix.follows = "tooling/fenix";
  inputs.flake-parts.follows = "tooling/flake-parts";
  inputs.devenv.follows = "tooling/devenv";
  inputs.treefmt-nix.follows = "tooling/treefmt-nix";
  inputs.git-hooks.follows = "tooling/git-hooks";
}
```

Consume in a flake-parts + devenv flake:

```nix
outputs = inputs@{ flake-parts, ... }: flake-parts.lib.mkFlake { inherit inputs; } {
  imports = [ inputs.devenv.flakeModule ];
  systems = [ "x86_64-linux" ];
  perSystem = { system, ... }: {
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
  };
}
```

Standalone: `nix-tooling` dogfoods its own modules via `devenv.shells.default` — see `flake.nix` `perSystem.devenv.shells.default`.

### Beads task tracking

Use the pinned CLI without entering a development shell:

```sh
nix run .#beads -- version
nix run .#beads -- --help
```

Consumers can add `inputs.tooling.packages.${system}.beads` to their packages or
opt into `inputs.tooling.devenvModules.beads`. The module only adds the CLI to
`PATH`: it does not initialize a database, install hooks, edit repository
instructions, start a server, or synchronize a remote.

The pin follows the [tested 1.2.2 release](https://github.com/gastownhall/beads/releases/tag/v1.2.2),
not the retracted 1.2.0/1.2.1 releases. Upstream's flake builds the embedded Dolt
engine into the CLI; using this package does not require a separate Dolt server.

Installing a CLI and upgrading an existing tracker are separate operations.
Before opening an older tracker, stop its writers and preserve its full `.beads`
directory, including ignored database files. Rehearse upgrades on a disposable
copy and compare issue exports before changing the original. JSONL exports are
useful for comparison but do not preserve full Dolt history. In 1.2.2 even a
read-only command can run version-upgrade initialization before opening its
read-only store; `version` and `--help` do not open the database.

Keep database synchronization and migration explicit. Do not run `bd init`,
`bd setup`, `bd hooks install`, `bd bootstrap`, or `bd dolt push` from shell entry
hooks. Hook installation must be reviewed separately to preserve repository
validation gates and commit-message policy.

The Rust module requires the pinned Fenix overlay and fails evaluation if it is absent. Build Rust packages with `pkgs.makeRustPlatform` using the same `pkgs.fenix.stable.cargo` and `pkgs.fenix.stable.rustc`. Consumers that expose a separate flake formatter should also select `pkgs.fenix.stable.rustfmt` in their `treefmt.config.programs.rustfmt.package`; enabling rustfmt alone selects nixpkgs' formatter. Static-musl packages need their target standard library and linker configured explicitly when sharing this compiler.

An application-independent bootstrap shell still needs devenv's task runner under the flakes integration. The pinned devenv task package deliberately uses its own locked nixpkgs and Rust toolchain to preserve upstream binary-cache compatibility. A source build can therefore fetch another compiler, Rust documentation, and native build tools even though the development shell uses the shared Fenix compiler. Bootstrap removes application build dependencies; it does not guarantee a small initial tooling download.

The [pinned devenv cache configuration](https://github.com/cachix/devenv/blob/97135e80b6e432f41f84f72383e1b8147f33ef0c/flake.nix) provides the official cache and signing key. They can be supplied for a single shell invocation without changing host configuration:

```sh
./scripts/devenv-shell.sh \
  --extra-substituters https://devenv.cachix.org \
  --extra-trusted-public-keys 'devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw='
```

> `rust.nix` hooks are **opt-in** (`git-hooks.hooks.rustfmt/clippy` default `false` via `lib.mkDefault`); consumers that want pre-commit rustfmt/clippy must enable them explicitly (e.g. `git-hooks.hooks.rustfmt.enable = true; git-hooks.hooks.clippy.enable = true;`). `treefmt.config.programs.rustfmt` remains enabled (formatter).

## Checks

- `nix fmt` → treefmt wrapper (nixfmt, statix, rustfmt, tombi)
- The [full check recipe](#development) validates all outputs and runs the eight ordinary checks: formatting, hooks, Tombi, Beads, and both guest contracts. Tombi sync rejects drift from `share/tombi-format.toml`.
- `nix build .#checks.x86_64-linux.beads-version` → verifies the pinned CLI version and help without creating a tracker
- `nix build .#checks.x86_64-linux.beads-embedded` → creates, exports, and queries one issue in disposable embedded storage without installing hooks or repository instructions
- `./scripts/devenv-shell.sh` → devenv shell with all tooling (bare `nix develop` no longer evaluates: pure eval cannot resolve devenv.root without the `devenv-root` input override)

Whole-flake validation also evaluates the development shell, so bare
`nix flake check` needs the explicit root input shown below. The repository's
unused automatic shell/process container outputs are disabled; shared modules
retain consumers' container capability. Disabling those outputs does not remove
the development shell's explicit-root requirement. Checking does not enter the
shell or run its hooks, and the optional NixOS VM test is not an ordinary check.

## Git hooks

In-shell enforcement comes from the `devenvModules` (`git-hooks.hooks.*` install
on shell entry with the pinned toolchain). For commits outside a devshell
(bare host, container without nix, agents that never load `.envrc`),
`.git/hooks/pre-commit` and `.git/hooks/pre-push` are pure-sh fallbacks
(canonical copies: `scripts/git-hooks/pre-commit.sh` and
`scripts/git-hooks/pre-push.sh`; reinstall with
`cp scripts/git-hooks/pre-commit.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`
and `cp scripts/git-hooks/pre-push.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push`):
secret-material greps always run, tier-1 linters (typos/nixfmt/statix/deadnix)
and tombi gates skip with a message when the tool is absent or
version-mismatched, staged `.rs` files get `rustfmt --check` at the crate's
edition when rustfmt is on PATH, pre-push runs `just check` when
just+nix+check-recipe are present (skip-with-message otherwise), and `prek`
delegation happens only when both the binary and a repo-root
`.pre-commit-config.yaml` exist. Plain `git commit`
never needs `--no-verify`. The generated `.pre-commit-config.yaml` is
gitignored and never committed (a copy referencing `/nix/store` paths dangles
after GC).

Canonical note: see workestrate `docs/nix/store-hygiene-and-gc.md`
§"Git hooks vs GC" — the devenv-shell clobber of these shims is disabled by
default (`git-hooks.install.enable = lib.mkDefault false` in
`devenvModules/base.nix`); if a consumer re-enables installation, re-run the
`cp` commands above after shell entry.

## Development

```sh
export PATH="/nix/store/<hash>-nix-<version>/bin:$PATH" # only needed when nix is not already on PATH
nix flake lock
tooling_root_file="$(mktemp)"
trap 'rm -f "$tooling_root_file"' EXIT
printf '%s' "$PWD" > "$tooling_root_file"
nix flake check --no-update-lock-file \
  --option allow-import-from-derivation false --max-jobs 1 --cores 2 \
  --override-input devenv-root "file+file://$tooling_root_file"
nix fmt
./scripts/devenv-shell.sh -c tombi --version
```
