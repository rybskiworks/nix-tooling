# base.nix — shared shell fundamentals via devenv.
#
# Provides:
# - core packages (git, jq)
# - typos spell-check via git-hooks
# - treefmt integration baseline (enabled, projectRootFile handling)
#
# This is a devenv module: `function { pkgs, lib, config, ... }: { ... }`
# It is imported via `devenv.shells.default.imports = [ inputs.tooling.devenvModules.base ]`
# from both nix-tooling (dogfooding) and consumers like workestrate.
#
# NOTE: git-hooks.enable is auto-enabled when any hook is enabled (devenv checks
# `anyHookEnabled`). Explicit `git-hooks.enable = true` is not required here.
{
  pkgs,
  lib,
  ...
}:
{
  packages = with pkgs; [
    git
    jq
  ];

  # Typos spell-checker: shared baseline. Consumers can override
  # `git-hooks.hooks.typos.settings.*` or disable via `git-hooks.hooks.typos.enable = false`.
  git-hooks.hooks.typos = {
    enable = lib.mkDefault true;
    # Use default typos settings; consumers may add `settings.config` overrides.
  };

  # Treefmt integration baseline (devenv's treefmt, not perSystem.treefmt).
  # Actual formatters are enabled in the more specific nix/toml/rust modules.
  # `treefmt.enable` makes `treefmt` available as a package and wires
  # `git-hooks.hooks.treefmt.package` to the treefmt wrapper (via devenv's
  # treefmt integration). `projectRootFile` is auto-set to "" (treeRoot)
  # by devenv; override if a custom file is needed.
  treefmt.enable = lib.mkDefault true;

  # Ensure devenv's treefmt wrapper is used for the treefmt git hook.
  # This is the default via devenv's treefmt integration, but we keep it explicit.
  # Individual modules (nix, toml, rust) populate `treefmt.config.*`.
}
