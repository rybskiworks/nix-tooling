# nix-tooling

Shared Nix dev tooling for the `rybskiworks` flakes: formatter, git-hooks, Tombi, and Rust via fenix.

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
│   └── rust.nix              # fenix stable toolchain + rustfmt/clippy (hooks opt-in, mkDefault false)
├── tombi.toml
└── README.md
```

### Inputs and version pins

| Input | Rev | Follows | Notes |
|-------|-----|---------|-------|
| `nixpkgs` | `a799d3e3886da994fa307f817a6bc705ae538eeb` (nixos-unstable) | — | Shared with workestrate for store-path deduplication (consumer sets `tooling.inputs.nixpkgs.follows = "nixpkgs"`). |
| `flake-parts` | `9d0d87172c374f89da73c1cfe6d81ae62feac1f1` | `nixpkgs` | |
| `devenv` | `97135e80b6e432f41f84f72383e1b8147f33ef0c` | `nixpkgs`, `git-hooks`, `flake-parts` | |
| `treefmt-nix` | `27b3b12a8e6375f28ebe122f07d230ca5459bbfa` | `nixpkgs` | No `programs.tombi` (128 programs checked). |
| `git-hooks.nix` | `27555e2624241fb116b49095df4caaee85a25691` | `nixpkgs` | No `hooks.tombi`; `hooks.treefmt.settings.fail-on-change` defaults `true`. |
| `fenix` | `fa09e6473a0dfd673e6cb9a37741aec513b4bb2a` (rustc 1.97.1) | `nixpkgs` (tooling) | **Owned pin** — consumer must NOT `follows`-override `tooling.inputs.fenix` so the toolchain stays reproducible. |

*Follows discipline*: share the consumer's `nixpkgs`, but don't override versions nix-tooling explicitly owns (curated pins like Tombi (share/tombi-version) and fenix). See `flake.nix` comments for decision log.

> `fenix`'s own `nixpkgs` is set to follow `nixpkgs` inside `nix-tooling` so its dependencies are built against the same nixpkgs, but `workestrate` does **not** set `tooling.inputs.fenix.follows = "nixpkgs"` — that would not override the fenix *rev* but would still couple toolchain closure to the consumer's nixpkgs bump; we document that as an owned pin.

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

Add as a flake input:

```nix
{
  inputs.tooling.url = "path:../nix-tooling"; # locally
  # inputs.tooling.url = "github:rybskiworks/nix-tooling"; # after publish
  inputs.tooling.inputs.nixpkgs.follows = "nixpkgs";
  # DO NOT: inputs.tooling.inputs.fenix.follows = "nixpkgs"; # own pin
}
```

Consume in a flake-parts + devenv flake:

```nix
outputs = inputs@{ flake-parts, ... }: flake-parts.lib.mkFlake { inherit inputs; } {
  imports = [ inputs.devenv.flakeModule ];
  systems = [ "x86_64-linux" ];
  perSystem = { config, pkgs, ... }: {
    devenv.shells.default.imports = [
      inputs.tooling.devenvModules.base
      inputs.tooling.devenvModules.nix
      inputs.tooling.devenvModules.toml
      inputs.tooling.devenvModules.rust
    ];
    formatter = config.treefmt.build.wrapper;
    checks = {
      inherit (config.pre-commit) checks;
      # plus your own checks
    };
  };
}
```

Standalone: `nix-tooling` dogfoods its own modules via `devenv.shells.default` — see `flake.nix` `perSystem.devenv.shells.default`.

> `rust.nix` hooks are **opt-in** (`git-hooks.hooks.rustfmt/clippy` default `false` via `lib.mkDefault`); consumers that want pre-commit rustfmt/clippy must enable them explicitly (e.g. `git-hooks.hooks.rustfmt.enable = true; git-hooks.hooks.clippy.enable = true;`). `treefmt.config.programs.rustfmt` remains enabled (formatter).

## Checks

- `nix fmt` → treefmt wrapper (nixfmt, statix, rustfmt, tombi)
- `nix flake check` → `checks.treefmt`, `checks.pre-commit`, `checks.tombiCheck`, `checks.tombi-sync` (tombi-sync fails on drift between the vendored `tombi.toml` and canonical `share/tombi-format.toml`)
- `./scripts/devenv-shell.sh` → devenv shell with all tooling (bare `nix develop` no longer evaluates: pure eval cannot resolve devenv.root without the `devenv-root` input override)

## Git hooks

In-shell enforcement comes from the `devenvModules` (`git-hooks.hooks.*` install
on shell entry with the pinned toolchain). For commits outside a devshell
(bare host, container without nix, agents that never load `.envrc`),
`.git/hooks/pre-commit` is a pure-sh fallback (canonical copy:
`scripts/git-hooks/pre-commit.sh`; reinstall with
`cp scripts/git-hooks/pre-commit.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`):
secret-material greps always run, tombi gates skip with a message when `tombi`
is absent or version-mismatched, and `prek` delegation happens only when both
the binary and a repo-root `.pre-commit-config.yaml` exist. Plain `git commit`
never needs `--no-verify`. The generated `.pre-commit-config.yaml` is
gitignored and never committed (a copy referencing `/nix/store` paths dangles
after GC).

Canonical note: see workestrate `docs/nix/store-hygiene-and-gc.md`
§"Git hooks vs GC" — entering the devenv shell moves this shim to
`pre-commit.legacy` and installs the generated hook; re-run the `cp` above
afterwards.

## Development

```sh
export PATH="/nix/store/<hash>-nix-<version>/bin:$PATH" # only needed when nix is not already on PATH
nix flake lock
nix flake check
nix fmt
./scripts/devenv-shell.sh -c tombi --version
```
