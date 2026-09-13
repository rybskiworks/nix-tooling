# A client-only shell addition; host daemon selection remains an operator action.
{ pkgs, ... }:
{
  packages = [ (pkgs.lib.getBin pkgs.lixPackageSets.stable.lix) ];
}
