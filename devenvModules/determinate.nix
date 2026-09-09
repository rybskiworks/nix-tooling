# Add the pinned client only; daemon installation and policy belong to the host.
determinate:
{ pkgs, ... }:
{
  # An unspecified multi-output package selects its development output in
  # nativeBuildInputs. Shell users need the executable, not Nix's C++ SDK.
  packages = [
    (pkgs.lib.getBin determinate.inputs.nix.packages.${pkgs.stdenv.hostPlatform.system}.default)
  ];
}
