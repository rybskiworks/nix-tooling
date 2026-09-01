# nix.nix — Nix formatting/linting via treefmt + git-hooks.
#
# Enables nixfmt (RFC 166 style, v1.0+) and statix/deadnix handling.
#
# Behaviour:
# - `treefmt.config.programs.nixfmt` provides `nix fmt` formatting (treefmt-nix).
# - `treefmt.config.programs.statix` provides statix fixes via treefmt.
# - `git-hooks.hooks.nixfmt` and `statix` provide pre-commit checks.
# - `deadnix` is also enabled as a lightweight lint (optional).
#
# Devenv module (not flake-parts). Import via `devenvModules.nix`.
{
  lib,
  ...
}:
{
  # treefmt-nix formatters (devenv's treefmt.config proxies to treefmt-nix)
  treefmt.config.programs.nixfmt.enable = lib.mkDefault true;
  treefmt.config.programs.statix.enable = lib.mkDefault true;
  # deadnix is available as a treefmt program as well, but we prefer git-hooks for it.

  # git-hooks: pre-commit hooks (auto-enables git-hooks.enable).
  # Note: `hooks.nixfmt` expects nixfmt >= 1.0 (RFC style). For classic (0.x) use `hooks.nixfmt-classic`.
  git-hooks.hooks = {
    nixfmt.enable = lib.mkDefault true;
    statix.enable = lib.mkDefault true;
    deadnix.enable = lib.mkDefault true;
  };
}
