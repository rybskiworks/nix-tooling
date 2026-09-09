{
  pkgs,
  nixpkgs,
  determinate,
}:
let
  inherit (pkgs) lib;
  profile = import ../../nixosModules/determinate-guest.nix { inherit determinate nixpkgs; };
  engine = determinate.inputs.nix.packages.${pkgs.stdenv.hostPlatform.system}.default;
  nixd = determinate.packages.${pkgs.stdenv.hostPlatform.system}.default;
  markedPkgs = pkgs.extend (_final: _prev: { guestPackageSetMarker = "shared-consumer"; });
  guest = import ../../lib/guest { inherit nixpkgs; };
  evaluated = guest.mkNixosSystem {
    pkgs = markedPkgs;
    stateVersion = "26.05";
    modules = [
      profile
      ({ pkgs, ... }: { system.nixos.label = pkgs.guestPackageSetMarker; })
    ];
  };
  cfg = evaluated.config;
  consumer = guest.mkNixosSystem {
    inherit pkgs;
    stateVersion = "26.05";
    modules = [
      profile
      {
        nix = {
          settings.substituters = [ "https://cache.example.invalid" ];
          settings.trusted-public-keys = [
            "example.invalid-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
          ];
          registry.nixpkgs.flake = determinate.inputs.nix.inputs.nixpkgs;
        };
      }
    ];
  };
  daemon = cfg.systemd.services.nix-daemon;
  nixdConfig = builtins.fromJSON cfg.environment.etc."determinate/config.json".text;
  assertions = {
    distributionRevision = determinate.rev == "cb76ac22754f6b36c008a3c39477c174a146dd6b";
    engineVersion = engine.version == "3.22.3";
    nixdVersion = nixd.version == engine.version;
    engineIdentity = cfg.nix.package.drvPath == engine.drvPath;
    nixdIdentity = builtins.elem nixd cfg.environment.systemPackages;
    consumerPackageSet = cfg.system.nixos.label == "shared-consumer";
    supportedPairEnabled = cfg.nix.enable && cfg.determinate.enable;
    daemonCommand = builtins.elem "@${nixd}/bin/determinate-nixd determinate-nixd --nix-bin ${engine}/bin daemon" daemon.serviceConfig.ExecStart;
    customConfig = cfg.environment.etc."nix/nix.conf".target == "nix/nix.custom.conf";
    socketOwned =
      cfg.systemd.sockets.determinate-nixd.socketConfig.ListenStream
      == "/nix/var/determinate/determinate-nixd.socket";
    sandbox = cfg.nix.settings.sandbox && !cfg.nix.settings.sandbox-fallback;
    signatures = cfg.nix.settings.require-sigs;
    rootTrust = lib.unique cfg.nix.settings.trusted-users == [ "root" ];
    explicitUsers =
      cfg.nix.settings.allowed-users == [
        "root"
        "@users"
      ];
    pinnedRegistry =
      cfg.nix.registry.nixpkgs.flake.outPath == nixpkgs.outPath && cfg.nix.settings.flake-registry == "";
    publicCaches =
      lib.sort builtins.lessThan cfg.nix.settings.substituters == [
        "https://cache.nixos.org/"
        "https://install.determinate.systems"
      ];
    noAdditionalDeclaredTrustedCaches = cfg.nix.settings.trusted-substituters == [ ];
    noImplicitTrust = !cfg.nix.settings.accept-flake-config;
    consumerCache =
      lib.sort builtins.lessThan consumer.config.nix.settings.substituters == [
        "https://cache.example.invalid"
        "https://cache.nixos.org/"
        "https://install.determinate.systems"
      ];
    consumerKey =
      builtins.elem "example.invalid-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" consumer.config.nix.settings.trusted-public-keys
      && builtins.all (
        key: builtins.elem key consumer.config.nix.settings.trusted-public-keys
      ) cfg.nix.settings.trusted-public-keys;
    consumerLockedRegistry =
      consumer.config.nix.registry.nixpkgs.flake.outPath == determinate.inputs.nix.inputs.nixpkgs.outPath;
    noCollector =
      !cfg.nix.gc.automatic
      && !cfg.nix.optimise.automatic
      && nixdConfig.garbageCollector.strategy == "disabled";
    noNetrc = nixdConfig.authentication.additionalNetrcSources == [ ];
    noMacBuilder = nixdConfig.builder.state == "disabled";
    noCrashEndpoint = nixdConfig.telemetry.sentry.endpoint == null;
    daemonPrivacy =
      daemon.environment.DETSYS_IDS_TELEMETRY == "disabled"
      && daemon.environment.NIX_SENTRY_ENDPOINT == "";
    clientPrivacy =
      cfg.environment.sessionVariables.DETSYS_IDS_TELEMETRY == "disabled"
      && cfg.environment.sessionVariables.NIX_SENTRY_ENDPOINT == "";
    noAutomaticUpgrade = !cfg.system.autoUpgrade.enable;
  };
in
{
  contract =
    assert lib.assertMsg (builtins.all (value: value) (builtins.attrValues assertions)) (
      "Determinate guest contract failed: "
      + lib.concatStringsSep ", " (builtins.attrNames (lib.filterAttrs (_name: value: !value) assertions))
    );
    pkgs.writeText "determinate-guest-contract.json" (builtins.toJSON assertions);

  nixos = import ./nixos.nix {
    inherit
      pkgs
      profile
      engine
      nixd
      ;
  };
}
