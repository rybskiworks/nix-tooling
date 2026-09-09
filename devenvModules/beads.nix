# Provide the pinned CLI without opening databases or installing hooks.
beadsInput:
{ pkgs, ... }:
{
  packages = [ beadsInput.packages.${pkgs.stdenv.hostPlatform.system}.bd ];
}
