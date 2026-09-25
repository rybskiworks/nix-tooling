nixpkgs:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  engine = nixpkgs.legacyPackages.${pkgs.stdenv.hostPlatform.system}.lixPackageSets.stable.lix;
in
{
  assertions = [
    {
      assertion = !(config.determinate.enable or false);
      message = "The Lix system profile cannot be combined with Determinate Nixd.";
    }
    {
      assertion = config.nix.package.drvPath == engine.drvPath;
      message = "The Lix system profile requires the shared stable Lix package.";
    }
  ];

  # Stable Lix 2.94.2 supplies a service/socket but no per-connection template.
  systemd.services."nix-daemon@".enable = lib.mkIf (engine.version == "2.94.2") false;

  # Hardware, networking, maintenance and resource limits belong to the role.
  nix = {
    enable = true;
    package = engine;
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
