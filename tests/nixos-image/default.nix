{
  pkgs,
  guest,
  base,
  nixd,
}:
let
  leaf = guest.mkNixosLayer {
    inherit pkgs base;
    name = "registered-guest-example";
    tag = "test";
    registrationName = "example";
    contents = [ pkgs.hello ];
    config.Cmd = [ "${pkgs.hello}/bin/hello" ];
  };
  grandchild = guest.mkNixosLayer {
    inherit pkgs;
    base = leaf;
    name = "registered-guest-grandchild";
    tag = "test";
    registrationName = "grandchild";
    contents = [ pkgs.hello ];
    maxLayers = leaf.guestLayerBudget + 2;
  };
  cfg = base.guestSystem.config;
  assertions = {
    kernelDisabled = !cfg.boot.kernel.enable;
    containerUserspace = cfg.boot.isContainer;
    firmwarePidRangePreserved = cfg.boot.kernel.sysctl."kernel.pid_max" == null;
    noGeneratedPidRangeTuning =
      !(pkgs.lib.hasInfix "kernel.pid_max" cfg.environment.etc."sysctl.d/60-nixos.conf".text);
    otherKernelTuningsRetained =
      cfg.boot.kernel.sysctl."kernel.kptr_restrict" == 1
      && cfg.boot.kernel.sysctl."vm.max_map_count" == 1048576
      && pkgs.lib.hasInfix "kernel.kptr_restrict=1\n" cfg.environment.etc."sysctl.d/60-nixos.conf".text
      && builtins.elem "multi-user.target" cfg.systemd.services.systemd-sysctl.wantedBy;
    explicitConsumerPidRangeAllowed =
      (base.guestSystem.extendModules {
        # An evaluation-only value, not a claim about any firmware's limit.
        modules = [ { boot.kernel.sysctl."kernel.pid_max" = 123456; } ];
      }).config.boot.kernel.sysctl."kernel.pid_max" == 123456;
    engineEnabled = cfg.nix.enable;
    determinateEnabled = cfg.determinate.enable;
    sharedDevenvCache =
      pkgs.lib.sort builtins.lessThan cfg.nix.settings.substituters == pkgs.lib.sort builtins.lessThan [
        "https://cache.nixos.org/"
        "https://install.determinate.systems"
        "https://devenv.cachix.org"
      ];
    sharedDevenvPublicKey =
      pkgs.lib.sort builtins.lessThan cfg.nix.settings.trusted-public-keys
      == pkgs.lib.sort builtins.lessThan [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
        "cache.flakehub.com-3:hJuILl5sVK4iKm86JzgdXW12Y2Hwd5G07qKtHTOcDCM="
        "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
      ];
    cacheDoesNotChangeClientAuthority =
      cfg.nix.settings.trusted-substituters == [ ]
      && pkgs.lib.unique cfg.nix.settings.trusted-users == [ "root" ]
      && cfg.nix.settings.require-sigs
      && cfg.nix.settings.sandbox
      && !cfg.nix.settings.sandbox-fallback
      && !cfg.nix.settings.accept-flake-config;
    samePkgs = base.guestSystem.pkgs.path == pkgs.path;
    generatedInit = base.guestInit.executable == "/init";
    parentExact = leaf.guestBase.outPath == base.outPath;
    sameInit = leaf.guestInit == base.guestInit;
    baseLeavesLayerRoom = base.guestLayerBudget == 64;
    leafHasPayloadAndCustomizationRoom = leaf.guestLayerBudget >= base.guestLayerBudget + 2;
    additiveRegistration =
      leaf.guestRegistrationNames == [
        "base"
        "example"
      ];
    distinctPayloadRoots = leaf.guestRegistration.roots != base.guestRegistration.roots;
    configRootIncluded = builtins.elem leaf.guestRegistration.runtimeConfig leaf.guestRegistration.roots;
    baseStoreInventory = base.guestStoreLayerConfigs == [ base.stream.conf ];
    leafStoreInventory =
      leaf.guestStoreLayerConfigs == base.guestStoreLayerConfigs ++ [ leaf.stream.conf ];
    grandchildStoreInventory =
      grandchild.guestStoreLayerConfigs == leaf.guestStoreLayerConfigs ++ [ grandchild.stream.conf ];
    fullLeafRegistrationRetained =
      leaf.guestRegistration.roots == [
        leaf.guestRegistration.payload
        leaf.guestRegistration.runtimeConfig
      ]
      &&
        grandchild.guestRegistration.roots == [
          grandchild.guestRegistration.payload
          grandchild.guestRegistration.runtimeConfig
        ];
    runtimeHosts = !cfg.environment.etc.hosts.enable;
    runtimeResolver = !cfg.environment.etc."resolv.conf".enable;
    runtimeHostname = !cfg.environment.etc.hostname.enable;
    runtimeCA = !cfg.security.pki.installCACerts;
    daemonCA =
      cfg.systemd.services.nix-daemon.environment.CURL_CA_BUNDLE == "/etc/ssl/certs/ca-certificates.crt";
    noDHCP = !cfg.networking.useDHCP && !cfg.networking.dhcpcd.enable;
    noCompetingNetwork = !cfg.systemd.network.enable && !cfg.services.resolved.enable;
    requiredDaemon = builtins.elem "guest-store-registration.service" cfg.systemd.services.nix-daemon.requires;
    requiredSocket = builtins.elem "guest-store-registration.service" cfg.systemd.sockets.nix-daemon.requires;
    requiredNixdSocket = builtins.elem "guest-store-registration.service" cfg.systemd.sockets.determinate-nixd.requires;
    earlyRegistration = !cfg.systemd.services.guest-store-registration.unitConfig.DefaultDependencies;
    duplicateRegistrationRejected =
      !(builtins.tryEval (
        guest.mkNixosLayer {
          inherit pkgs base;
          name = "invalid-example";
          registrationName = "base";
        }
      )).success;
    invalidRegistrationRejected =
      !(builtins.tryEval (
        guest.mkNixosLayer {
          inherit pkgs base;
          name = "invalid-example";
          registrationName = "../escape";
        }
      )).success;
    insufficientLayerBudgetRejected =
      !(builtins.tryEval (
        guest.mkNixosLayer {
          inherit pkgs base;
          name = "invalid-example";
          registrationName = "insufficient";
          maxLayers = base.guestLayerBudget + 1;
        }
      )).success;
  };
  report = builtins.toJSON assertions;
  smokeSource = pkgs.lib.fileset.toSource {
    root = ../.;
    fileset = pkgs.lib.fileset.unions [
      ./msb_smoke.py
      ./smoke_contract.py
      ./smoke_full.py
      ./shutdown_diagnostic.py
      ./test_smoke_contract.py
      ./test_shutdown_diagnostic.py
      ./fixtures/msb-inspect.json
      ../support/children.py
      ../support/test_children.py
    ];
  };
  inheritanceSpec = pkgs.writeText "nixos-image-layer-inheritance.json" (
    builtins.toJSON (
      map
        (
          { image, reusedRoots }:
          let
            # Compute the complete closure independently of the constructor's
            # registration passthru, so a pruned registration cannot pass.
            roots = [
              image.guestRegistration.payload
              image.guestRegistration.runtimeConfig
            ];
            complete = pkgs.closureInfo { rootPaths = roots; };
          in
          {
            archive = toString image;
            streamConfig = toString image.stream.conf;
            registrationName = image.guestRegistration.registrationName;
            roots = map toString roots;
            fullRegistration = "${complete}/registration";
            fullStorePaths = "${complete}/store-paths";
            reusedRoots = map toString reusedRoots;
            maxLayers = image.guestLayerBudget;
          }
        )
        [
          {
            image = base;
            reusedRoots = [ ];
          }
          {
            image = leaf;
            reusedRoots = [ pkgs.glibc ];
          }
          {
            image = grandchild;
            reusedRoots = [
              pkgs.glibc
              pkgs.hello
            ];
          }
        ]
    )
  );
in
assert builtins.all (value: value) (builtins.attrValues assertions);
{
  inherit
    base
    leaf
    grandchild
    assertions
    ;
  # Explicitly opt-in: this check realizes and streams three actual archives.
  # It is deliberately not a dependency of the ordinary source contract below.
  layerInheritance = pkgs.runCommand "nixos-image-layer-inheritance" { } ''
    mkdir -p "$out"
    ${pkgs.python3}/bin/python3 -B ${./check_layer_inheritance.py} \
      ${inheritanceSpec} > "$out/result.json"
  '';
  # Metadata only: the caller explicitly realizes/verifies the archive first.
  smokeSpec = pkgs.writeText "nixos-msb-smoke-spec.json" (
    builtins.unsafeDiscardStringContext (
      builtins.toJSON {
        version = 1;
        archive = base.outPath;
        toplevel = base.guestToplevel.outPath;
        engine = cfg.nix.package.outPath;
        nixd = nixd.outPath;
        bash = pkgs.bash.outPath;
        coreutils = pkgs.coreutils.outPath;
        systemd = cfg.systemd.package.outPath;
        glibc = pkgs.glibc.bin.outPath;
        utilLinux = pkgs.util-linux.outPath;
        registration = base.guestRegistration.closure.outPath;
        roots = map toString base.guestRegistration.roots;
      }
    )
  );
  # Neither archive nor the system toplevel is a dependency of this small check.
  contract =
    pkgs.runCommand "nixos-image-contract"
      {
        nativeBuildInputs = [
          pkgs.python3
          pkgs.bash
          pkgs.coreutils
          pkgs.util-linux
        ];
        report = builtins.unsafeDiscardStringContext report;
      }
      ''
        mkdir -p "$out"
        printf '%s\n' "$report" > "$out/contract.json"
        export REGISTER_SCRIPT=${../../lib/guest/register-store.sh}
        export LEAF_CHECK_SCRIPT=${../../lib/guest/check-leaf.sh}
        export LAYER_PIPELINE_SCRIPT=${../../lib/guest/layer-pipeline.py}
        export LAYER_INHERITANCE_SCRIPT=${./check_layer_inheritance.py}
        python ${./test_registration.py} -v
        python -B ${./test_layer_pipeline.py} -v
        python -B ${./test_layer_inheritance.py} -v
        python -B -m unittest discover -s ${smokeSource}/support -v
        python -B -m unittest discover -s ${smokeSource}/nixos-image -p test_smoke_contract.py -v
        python -B -m unittest discover -s ${smokeSource}/nixos-image -p test_shutdown_diagnostic.py -v
        touch "$out/ok"
      '';
}
