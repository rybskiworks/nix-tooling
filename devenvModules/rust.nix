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
    else if pkgs ? fenix && pkgs.fenix ? complete then
      pkgs.fenix.complete.toolchain
    else
      pkgs; # fallback; will error visibly if fenix overlay missing

  # Derive rustfmt package for treefmt. Prefer fenix's rustfmt if available.
  rustfmtPkg = fenixToolchain.rustfmt or pkgs.rustfmt or pkgs.rustPackages.rustfmt or null;
in
{
  # Disable devenv's languages.rust (rust-overlay channel) — we own the toolchain via fenix.
  languages.rust.enable = lib.mkDefault false;

  packages = lib.mkMerge [
    (lib.mkIf (pkgs ? fenix && pkgs.fenix ? stable) [
      fenixToolchain.cargo
      fenixToolchain.rustc
      fenixToolchain.clippy
      fenixToolchain.rustfmt
      fenixToolchain.rust-analyzer
    ])
    # Include rust-src for rust-analyzer if available (not all fenix sets expose it at top-level)
    (lib.mkIf (pkgs ? fenix && pkgs.fenix ? stable && fenixToolchain ? rust-src) [
      fenixToolchain.rust-src
    ])
    (lib.mkIf (!(pkgs ? fenix && pkgs.fenix ? stable)) [
      # Fallback if overlay not applied (should not happen in normal flake use)
      pkgs.cargo
      pkgs.rustc
      pkgs.clippy
      pkgs.rustfmt
      pkgs.rust-analyzer
    ])
  ];

  treefmt.config.programs.rustfmt = lib.mkMerge [
    {
      enable = lib.mkDefault true;
      # treefmt-nix's rustfmt default is edition 2024, which treats `gen` as
      # reserved and breaks pre-2024 crates (e.g. workestrate's
      # control/agentctl, which pins edition = "2021" in its Cargo.toml and
      # re-pins 2021 in its own treefmt.config). mkDefault so consumers on
      # newer editions can override.
      edition = lib.mkDefault "2021";
    }
    (lib.mkIf (rustfmtPkg != null) { package = lib.mkDefault rustfmtPkg; })
  ];

  git-hooks.hooks = {
    rustfmt.enable = lib.mkDefault false;
    clippy = {
      enable = lib.mkDefault false;
      # clippy hook defaults to `cargo clippy --all-targets -- -D warnings` but we keep default.
      settings.denyWarnings = lib.mkDefault true;
    };
  };
}
