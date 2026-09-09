{ lib, ... }:
{
  # A NixOS-derived system is not a booted or activated NixOS guest.
  boot.isContainer = lib.mkDefault true;
  networking.firewall.enable = lib.mkDefault false;
  documentation.enable = lib.mkDefault false;
  environment.defaultPackages = lib.mkDefault [ ];

  # Engine, daemon and registration policy are a separate explicit profile.
  nix.enable = lib.mkDefault false;
  nix.channel.enable = lib.mkDefault false;
}
