nixpkgs:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  engine = config.nix.package;
in
{
  assertions = [
    {
      assertion = !(config.determinate.enable or false);
      message = "The Lix system profile cannot be combined with Determinate Nixd.";
    }
    {
      assertion = (engine.pname or "") == "lix";
      message = "The Lix system profile requires nix.package to be a Lix package.";
    }
    {
      assertion = lib.meta.availableOn pkgs.stdenv.hostPlatform engine;
      message = "The selected Lix package must support the consumer's host platform.";
    }
  ];

  # Stable Lix 2.94.2 supplies a service/socket but no per-connection template.
  systemd.services."nix-daemon@".enable = lib.mkIf (engine.version == "2.94.2") false;

  # Hardware, networking, maintenance and resource limits belong to the role.
  nix = {
    enable = true;
    package = lib.mkDefault pkgs.lixPackageSets.stable.lix;
    channel.enable = false;
    registry.nixpkgs.flake = lib.mkDefault nixpkgs;
    nixPath = lib.mkDefault [ ];
    settings = {
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      flake-registry = lib.mkDefault "";
      accept-flake-config = false;
      sandbox = true;
      sandbox-fallback = false;
      require-sigs = true;
      trusted-users = [ "root" ];
      allowed-users = [
        "root"
        "@users"
      ];
      trusted-substituters = [ ];
    };
  };
}
