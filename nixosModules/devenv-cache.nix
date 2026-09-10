devenv:
{ lib, ... }:
{
  # Read only the locked source declaration, not flake outputs or live metadata.
  # Importing this module explicitly trusts this one public signing authority.
  nix.settings = import ../lib/devenv-cache.nix {
    inherit lib;
    nixConfig = (import "${devenv}/flake.nix").nixConfig or { };
  };
}
