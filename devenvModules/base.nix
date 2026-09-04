# base.nix — shared shell fundamentals via devenv.
#
# Provides:
# - core packages (git, jq)
# - typos spell-check via git-hooks
# - treefmt integration baseline (enabled, projectRootFile handling)
# - git-hooks installer disabled by default (shell entry no longer mutates .git/hooks)
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

  # Disable git-hooks.nix's installer so entering a devenv shell no longer
  # mutates `.git/hooks`.
  #
  # Previously, entering any devenv shell that imports these modules ran
  # git-hooks.nix's installer, which MOVED the tracked pure-sh fallback
  # (`.git/hooks/pre-commit.sh`) to `.git/hooks/pre-commit.legacy` and wrote a
  # store-path'd generated hook in its place. That generated hook dangles after
  # nix GC and drops the secret-material gate the pure-sh fallback enforces —
  # so commits outside a live devshell would either hard-fail or silently lose
  # the secret guard.
  #
  # Setting `install.enable = false` stops the `.git/hooks` mutation while
  # keeping the hook definitions fully evaluated and usable: they remain
  # available to `nix flake check` (via the separate perSystem `pre-commit`
  # namespace in flake.nix, which is unaffected by this devenv-side knob) and
  # to devenv's task system.
  #
  # `mkDefault` so a consumer that genuinely wants installer-managed hooks may
  # deliberately re-enable via `git-hooks.install.enable = lib.mkForce true;`.
  git-hooks.install.enable = lib.mkDefault false;

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
