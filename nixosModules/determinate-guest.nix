{ determinate, nixpkgs }:
{ lib, ... }:
let
  privacy = {
    DETSYS_IDS_TELEMETRY = "disabled";
    NIX_SENTRY_ENDPOINT = "";
  };
in
{
  imports = [ determinate.nixosModules.default ];

  # Importing this profile opts into the supported engine and Nixd together.
  determinate.enable = true;
  nix = {
    enable = true;
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
      # Add to NixOS's public cache defaults; consumers may add reviewed caches.
      substituters = [ "https://install.determinate.systems" ];
      trusted-public-keys = [ "cache.flakehub.com-3:hJuILl5sVK4iKm86JzgdXW12Y2Hwd5G07qKtHTOcDCM=" ];
      max-jobs = lib.mkDefault 1;
      cores = lib.mkDefault 2;
    };
  };

  # No account, netrc aggregation, background collector or macOS builder.
  environment.etc."determinate/config.json".text = builtins.toJSON {
    authentication.additionalNetrcSources = [ ];
    builder.state = "disabled";
    garbageCollector.strategy = "disabled";
    telemetry.sentry.endpoint = null;
  };
  environment.sessionVariables = privacy;
  systemd.services.nix-daemon.environment = privacy;
}
