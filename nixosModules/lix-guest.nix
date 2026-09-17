nixpkgs:
{
  config,
  lib,
  pkgs,
  ...
}:
{
  assertions = [
    {
      assertion = !(config.determinate.enable or false);
      message = "The Lix guest profile cannot be combined with Determinate Nixd.";
    }
    {
      assertion = config.nix.package.drvPath == pkgs.lixPackageSets.stable.lix.drvPath;
      message = "The Lix guest profile requires the shared stable Lix package.";
    }
  ];
  # This nixpkgs module declares template overrides, but stable Lix 2.94.2
  # ships only the plain service/socket. Do not emit an ExecStart-less template.
  systemd.services."nix-daemon@".enable = lib.mkIf (
    pkgs.lixPackageSets.stable.lix.version == "2.94.2"
  ) false;
  # Use NixOS's ordinary daemon/build-user integration with the pinned Lix set.
  # This does not import the Determinate supervisor or install a host daemon.
  nix = {
    enable = true;
    package = pkgs.lixPackageSets.stable.lix;
    channel.enable = false;
    registry.nixpkgs.flake = lib.mkDefault nixpkgs;
    nixPath = lib.mkDefault [ ];
    gc.automatic = false;
    optimise.automatic = false;
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
      max-jobs = lib.mkDefault 1;
      cores = lib.mkDefault 2;
    };
  };
}
