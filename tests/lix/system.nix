{
  pkgs,
  nixpkgs,
  sopsModule,
  sopsInstaller,
  guestBase,
}:
let
  inherit (pkgs) lib;
  system = nixpkgs.lib.nixosSystem {
    modules = [
      (import ../../nixosModules/lix-system.nix nixpkgs)
      sopsModule
      {
        nixpkgs.pkgs = pkgs;
        system.stateVersion = "26.05";
        networking.hostName = "lix-system-test";
        networking.networkmanager.enable = true;
        boot.loader.grub.enable = false;
        fileSystems."/" = {
          device = "none";
          fsType = "tmpfs";
        };
        environment.defaultPackages = [ pkgs.hello ];
        nix = {
          gc.automatic = true;
          optimise.automatic = true;
          settings = {
            max-jobs = 3;
            cores = 4;
          };
        };
      }
    ];
  };
  cfg = system.config;
  replacement = system.extendModules {
    modules = [ { nix.package = lib.mkForce pkgs.nix; } ];
  };
  consumerEngine = pkgs.lixPackageSets.stable.lix.overrideAttrs (_old: {
    name = "lix-consumer-package-set";
  });
  consumerPkgs = pkgs.extend (
    _final: prev: {
      lixPackageSets = prev.lixPackageSets // {
        stable = prev.lixPackageSets.stable // {
          lix = consumerEngine;
        };
      };
    }
  );
  callerSystem = system.extendModules {
    modules = [ { nixpkgs.pkgs = lib.mkForce consumerPkgs; } ];
  };
  selectedEngine = pkgs.lixPackageSets.stable.lix.overrideAttrs (_old: {
    name = "lix-explicit-selection";
  });
  selectedSystem = system.extendModules {
    modules = [ { nix.package = selectedEngine; } ];
  };
  unsupportedPlatform = system.extendModules {
    modules = [
      {
        nix.package = pkgs.lixPackageSets.stable.lix.overrideAttrs (old: {
          meta = old.meta // {
            platforms = [ "aarch64-darwin" ];
          };
        });
      }
    ];
  };
  assertions = {
    validSystem = builtins.all (entry: entry.assertion) cfg.assertions;
    hostAndGuestEngine = cfg.nix.package.drvPath == guestBase.guestSystem.config.nix.package.drvPath;
    sharedEngine = cfg.nix.package.drvPath == pkgs.lixPackageSets.stable.lix.drvPath;
    unsupportedEngineRejected = !(builtins.all (entry: entry.assertion) replacement.config.assertions);
    callerPackageSetEngine = callerSystem.config.nix.package.drvPath == consumerEngine.drvPath;
    callerEngineDistinct = consumerEngine.drvPath != cfg.nix.package.drvPath;
    callerEngineAccepted = builtins.all (entry: entry.assertion) callerSystem.config.assertions;
    selectedEngine = selectedSystem.config.nix.package.drvPath == selectedEngine.drvPath;
    selectedEngineAccepted = builtins.all (entry: entry.assertion) selectedSystem.config.assertions;
    unsupportedPlatformRejected =
      !(builtins.all (entry: entry.assertion) unsupportedPlatform.config.assertions);
    hostKernelRetained = !cfg.boot.isContainer && cfg.boot.kernel.enable;
    hostNetworkRetained = cfg.networking.networkmanager.enable && cfg.networking.firewall.enable;
    hostNameRetained = cfg.networking.hostName == "lix-system-test";
    defaultPackagesRetained = builtins.elem pkgs.hello cfg.environment.defaultPackages;
    maintenanceOwnedByRole = cfg.nix.gc.automatic && cfg.nix.optimise.automatic;
    resourcesOwnedByRole = cfg.nix.settings.max-jobs == 3 && cfg.nix.settings.cores == 4;
    noGuestRegistration = !(cfg.systemd.services ? guest-store-registration);
    ordinaryDaemon = !(cfg.systemd.services.nix-daemon.serviceConfig ? ExecStart);
    pinnedRegistry = cfg.nix.registry.nixpkgs.flake.outPath == nixpkgs.outPath;
    secureBuildDefaults = cfg.nix.settings.sandbox && cfg.nix.settings.require-sigs;
    sopsInstallerShared = cfg.sops.package.drvPath == sopsInstaller.drvPath;
    noImplicitSecrets = cfg.sops.secrets == { };
    noImplicitAgeKey = cfg.sops.age.keyFile == null && !cfg.sops.age.generateKey;
    noSecretService = !(cfg.systemd.services ? sops-install-secrets);
  };
in
{
  inherit assertions;
  contract =
    assert lib.assertMsg (builtins.all (value: value) (builtins.attrValues assertions)) (
      "Lix system contract failed: "
      + lib.concatStringsSep ", " (builtins.attrNames (lib.filterAttrs (_name: value: !value) assertions))
    );
    pkgs.writeText "lix-system-contract.json" (builtins.toJSON assertions);
}
