nixpkgs:
{ lib, ... }:
{
  imports = [ (import ./lix-system.nix nixpkgs) ];

  nix = {
    gc.automatic = false;
    optimise.automatic = false;
    settings = {
      max-jobs = lib.mkDefault 1;
      cores = lib.mkDefault 2;
    };
  };
}
