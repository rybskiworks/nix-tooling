# rust.nix — Rust toolchain via fenix + rustfmt/clippy via treefmt & git-hooks.
#
# Uses fenix (nix-community/fenix, pinned to same rev as workestrate: fa09e647...)
# for a reproducible stable toolchain (currently rustc 1.97.1). The perSystem
# flake sets `_module.args.pkgs` with `fenix.overlays.default` so `pkgs.fenix.stable`
# is available here. Alternative `inputs.fenix.packages.${system}.stable` is also
# equivalent; the overlay form is preferred for devenv module ergonomics (no
# extra `inputs` argument needed).
#
# We intentionally do NOT use `languages.rust` (devenv's rust-overlay path) to avoid
# competing toolchain management. Instead we add fenix packages directly to `packages`
# and enable rustfmt/clippy via treefmt + git-hooks. `languages.rust.enable` is
# defaulted false to prevent accidental rust-overlay installs when this module is
# imported alongside others.
#
# Provides:
# - fenix stable toolchain: cargo, rustc, clippy, rustfmt, rust-analyzer, rust-src
# - treefmt.config.programs.rustfmt (uses fenix's rustfmt)
# - git-hooks.hooks.rustfmt + clippy
#
{
  pkgs,
  lib,
  ...
}:
let
  fenixToolchain =
    if pkgs ? fenix && pkgs.fenix ? stable then
      pkgs.fenix.stable
    else
      throw "nix-tooling's Rust module requires the pinned Fenix overlay in pkgs";
in
{
  # Disable devenv's languages.rust (rust-overlay channel) — we own the toolchain via fenix.
  languages.rust.enable = lib.mkDefault false;

  packages = [
    fenixToolchain.cargo
    fenixToolchain.rustc
    fenixToolchain.clippy
    fenixToolchain.rustfmt
    fenixToolchain.rust-analyzer
  ]
  ++ lib.optional (fenixToolchain ? rust-src) fenixToolchain.rust-src;

  treefmt.config.programs.rustfmt = {
    enable = lib.mkDefault true;
    # Codebase is edition-2024-clean by design (workestrate's
    # control/agentctl pins edition = "2024" in its Cargo.toml; `gen`
    # identifiers were renamed to `generator` in f20bfca). Default to the
    # modern edition; mkDefault so consumers on older editions can override.
    edition = lib.mkDefault "2024";
    package = lib.mkDefault fenixToolchain.rustfmt;
  };

  git-hooks.hooks = {
    rustfmt.enable = lib.mkDefault false;
    clippy = {
      enable = lib.mkDefault false;
      # clippy hook defaults to `cargo clippy --all-targets -- -D warnings` but we keep default.
      settings.denyWarnings = lib.mkDefault true;
    };
  };
}
