# toml.nix — Tombi TOML formatter/linter via custom treefmt formatter + git-hooks.
#
# Upstream status (validated 2026-09-01):
# - treefmt-nix latest (27b3b12...) has NO programs.tombi (128 programs checked, only taplo/toml-sort exist).
# - git-hooks.nix latest (27555e...) has NO hooks.tombi, but has hooks.treefmt with `settings.fail-on-change` default true.
#
# Design:
# - Provide a pinned Tombi package (version single-sourced in ../share/tombi-version, same pin as workestrate) via `pkgs.callPackage ../packages/tombi.nix`.
# - Register a custom treefmt formatter `tombi`:
#     treefmt.config.settings.formatter.tombi = {
#       command = "${tombi}/bin/tombi";
#       options = ["format"];
#       includes = ["*.toml"];
#     }
#   treefmt will then invoke `tombi format` for each *.toml file during `nix fmt` and via the treefmt git hook.
#
# - Git commit workflow:
#   The git-hooks treefmt hook by default runs `treefmt --fail-on-change --no-cache` which FAILS when formatting
#   is needed (it does NOT auto-fix). For an auto-fix workflow (ideal for developers), set
#     git-hooks.hooks.treefmt.settings.fail-on-change = false
#   then `git commit` will auto-format staged *.toml (and *.nix, *.rs) files in-place and allow the commit
#   (the formatted changes must be re-staged if the hook modifies the working tree; prek/pre-commit will
#   stage them as handled). If you prefer a fail-closed CI style, leave `fail-on-change = true` and run
#   `nix fmt` or `treefmt` manually before committing.
#   This module sets `fail-on-change = false` as the desired auto-fix default, documented above. Consumers
#   may override: `git-hooks.hooks.treefmt.settings.fail-on-change = lib.mkForce true;`
#
# - Separate lint hook:
#   `tombi lint --error-on-warnings` runs as a distinct hook `tombi-lint` (files = "\\.toml$") for lint
#   errors (dotted keys, ordering, schema warnings). It runs with `pass_filenames = false` so tombi lints the
#   whole repo per its tombi.toml include/exclude logic.
#
# Consumer usage:
#   devenv.shells.default.imports = [ inputs.tooling.devenvModules.toml ];
#
{
  pkgs,
  lib,
  ...
}:
let
  # Resolve tombi relative to this module's flake source.
  # When imported from a consumer (e.g. workestrate via path:../nix-tooling),
  # this path is still inside the nix-tooling store copy (`/nix/store/...-source/packages/tombi.nix`),
  # so the relative reference remains valid.
  tombi = pkgs.callPackage ../packages/tombi.nix { };
in
{
  packages = [ tombi ];

  # Custom treefmt formatter for TOML via Tombi.
  # treefmt-nix has no native `programs.tombi`, so we register via settings.formatter.
  treefmt.config.settings.formatter.tombi = {
    command = "${tombi}/bin/tombi";
    options = [ "format" ];
    includes = [ "*.toml" ];
  };

  git-hooks.hooks = {
    # Auto-fix staged files on commit via treefmt.
    # Default for treefmt-nix git hook is `fail-on-change = true` (CI fail-closed).
    # We default to false for a developer-friendly auto-format workflow.
    treefmt = {
      enable = lib.mkDefault true;
      settings.fail-on-change = lib.mkDefault false;
      settings.no-cache = lib.mkDefault true;
    };

    # Tombi lint: schema + rule checks, failing on warnings.
    # Separate from formatting so `tombi format` and `tombi lint` concerns don't conflate.
    # Use a custom hook name `tombi-lint`; there is no upstream `hooks.tombi`.
    tombi-lint = {
      enable = lib.mkDefault true;
      name = "tombi lint";
      entry = "${tombi}/bin/tombi lint --error-on-warnings";
      files = "\\.toml$";
      pass_filenames = false;
    };
  };
}
