{
  pkgs,
  nixpkgs,
  guest,
  base,
}:
let
  inherit (pkgs) lib;
  engine = pkgs.lixPackageSets.stable.lix;
  cfg = base.guestSystem.config;
  daemon = cfg.systemd.services.nix-daemon;
  profile = import ../../nixosModules/lix-guest.nix nixpkgs;
  clientModule = lib.evalModules {
    specialArgs = { inherit pkgs; };
    modules = [
      {
        options.packages = lib.mkOption {
          type = lib.types.listOf lib.types.package;
          default = [ ];
        };
      }
      ../../devenvModules/lix.nix
    ];
  };
  clients = clientModule.config.packages;
  markedPkgs = pkgs.extend (_final: _prev: { guestPackageSetMarker = "lix-consumer"; });
  consumer = guest.mkNixosSystem {
    pkgs = markedPkgs;
    stateVersion = "26.05";
    modules = [
      profile
      ({ pkgs, ... }: { system.nixos.label = pkgs.guestPackageSetMarker; })
      {
        nix.settings.substituters = [ "https://cache.example.invalid" ];
        nix.settings.trusted-public-keys = [
          "example.invalid-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        ];
      }
    ];
  };
  replacedEngine = base.guestSystem.extendModules {
    modules = [ { nix.package = lib.mkForce pkgs.nix; } ];
  };
  leaf = guest.mkNixosLayer {
    inherit pkgs base;
    name = "lix-registered-example";
    tag = "test";
    registrationName = "example";
    contents = [ pkgs.hello ];
    config.Cmd = [ "${pkgs.hello}/bin/hello" ];
  };
  assertions = {
    engineVersion = engine.version == "2.94.2";
    engineIdentity = cfg.nix.package.drvPath == engine.drvPath;
    engineNotCppNix = engine.drvPath != pkgs.nix.drvPath;
    moduleAssertions = builtins.all (entry: entry.assertion) cfg.assertions;
    engineReplacementRejected =
      !(builtins.all (entry: entry.assertion) replacedEngine.config.assertions);
    clientOnlyModule = builtins.length clients == 1;
    clientBinaryOutput = (builtins.head clients).outPath == (lib.getBin engine).outPath;
    clientNotDevelopmentOutput =
      (lib.getDev (builtins.head clients)).outPath == (lib.getBin engine).outPath;
    consumerPackageSet = consumer.config.system.nixos.label == "lix-consumer";
    samePkgs = base.guestSystem.pkgs.path == pkgs.path;
    engineEnabled = cfg.nix.enable;
    noDeterminate = !(cfg.determinate.enable or false);
    noNixdConfiguration = !(cfg.environment.etc ? "determinate/config.json");
    noNixdSocket = !(cfg.systemd.sockets ? determinate-nixd);
    upstreamDaemonUnits = builtins.elem engine.out cfg.systemd.packages;
    upstreamTmpfiles = builtins.elem engine.out cfg.systemd.tmpfiles.packages;
    upstreamExecNotReplaced = !(daemon.serviceConfig ? ExecStart);
    unsupportedTemplateMasked = !cfg.systemd.services."nix-daemon@".enable;
    ordinaryConfig = cfg.environment.etc."nix/nix.conf".target == "nix/nix.conf";
    daemonRoot = cfg.nix.daemonUser == "root" && cfg.nix.daemonGroup == "root";
    buildUsers = cfg.nix.nrBuildUsers > 0 && cfg.users.users.nixbld1.isSystemUser;
    sandbox = cfg.nix.settings.sandbox && !cfg.nix.settings.sandbox-fallback;
    signatures = cfg.nix.settings.require-sigs;
    rootTrust = lib.unique cfg.nix.settings.trusted-users == [ "root" ];
    ordinaryUsers =
      cfg.nix.settings.allowed-users == [
        "root"
        "@users"
      ];
    noAutomaticTrust =
      !cfg.nix.settings.accept-flake-config && cfg.nix.settings.trusted-substituters == [ ];
    sharedCaches =
      lib.sort builtins.lessThan cfg.nix.settings.substituters == [
        "https://cache.nixos.org/"
        "https://devenv.cachix.org"
      ];
    sharedCacheKeys =
      lib.sort builtins.lessThan cfg.nix.settings.trusted-public-keys == [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
        "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw="
      ];
    consumerCacheComposition =
      lib.sort builtins.lessThan consumer.config.nix.settings.substituters == [
        "https://cache.example.invalid"
        "https://cache.nixos.org/"
      ];
    consumerKeyComposition = builtins.elem "example.invalid-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" consumer.config.nix.settings.trusted-public-keys;
    registryPinned = cfg.nix.registry.nixpkgs.flake.outPath == nixpkgs.outPath;
    noFloatingRegistry = cfg.nix.settings.flake-registry == "";
    legacyLookupPinned = cfg.nix.nixPath == [ "nixpkgs=flake:nixpkgs" ];
    noAutomaticMaintenance =
      !cfg.nix.gc.automatic && !cfg.nix.optimise.automatic && !cfg.system.autoUpgrade.enable;
    noChannels = !cfg.nix.channel.enable;
    containerUserspace = cfg.boot.isContainer && !cfg.boot.kernel.enable;
    runtimeInit =
      base.guestInit.executable == "/init" && base.guestInit.env.container == "microsandbox";
    runtimeNetwork =
      !cfg.environment.etc.hosts.enable
      && !cfg.environment.etc."resolv.conf".enable
      && !cfg.networking.useDHCP;
    daemonRuntimeCA = daemon.environment.NIX_SSL_CERT_FILE == "/etc/ssl/certs/ca-certificates.crt";
    daemonRegistration = builtins.elem "guest-store-registration.service" daemon.requires;
    socketRegistration = builtins.elem "guest-store-registration.service" cfg.systemd.sockets.nix-daemon.requires;
    registrationEarly = !cfg.systemd.services.guest-store-registration.unitConfig.DefaultDependencies;
    leafExactParent = leaf.guestBase.outPath == base.outPath;
    leafSameSystem =
      leaf.guestToplevel.outPath == base.guestToplevel.outPath && leaf.guestInit == base.guestInit;
    leafSameEngine = leaf.guestSystem.config.nix.package.drvPath == engine.drvPath;
    registrationAdditive =
      leaf.guestRegistrationNames == [
        "base"
        "example"
      ];
    layerBudget = base.guestLayerBudget == 64 && leaf.guestLayerBudget >= base.guestLayerBudget + 2;
  };
in
{
  inherit assertions leaf;
  # Evaluation only: no client, system, image or VM realization dependency.
  contract =
    assert lib.assertMsg (builtins.all (value: value) (builtins.attrValues assertions)) (
      "Lix guest contract failed: "
      + lib.concatStringsSep ", " (builtins.attrNames (lib.filterAttrs (_name: value: !value) assertions))
    );
    pkgs.writeText "lix-guest-contract.json" (builtins.toJSON assertions);
  # Optional native client check; not part of ordinary tooling checks.
  client = pkgs.runCommand "lix-client-check" { nativeBuildInputs = clients; } ''
    export HOME="$TMPDIR/home" XDG_CONFIG_HOME="$TMPDIR/config"
    export NIX_USER_CONF_FILES=/dev/null
    mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$out"
    test "$(command -v nix)" = '${lib.getBin engine}/bin/nix'
    nix --version > "$out/version"
    grep -F 'nix (Lix, like Nix) ${engine.version}' "$out/version"
    test -f '${engine}/lib/systemd/system/nix-daemon.service'
    test -f '${engine}/lib/systemd/system/nix-daemon.socket'
    test ! -e '${engine}/lib/systemd/system/nix-daemon@.service'
    grep -Fx 'ExecStart=@${engine}/bin/nix-daemon nix-daemon --daemon' \
      '${engine}/lib/systemd/system/nix-daemon.service'
    grep -Fx 'ListenStream=/nix/var/nix/daemon-socket/socket' \
      '${engine}/lib/systemd/system/nix-daemon.socket'
  '';
  # Realize the archive and inspect the emitted system, not config booleans alone.
  image = pkgs.runCommand "lix-image-unit-check" { } ''
    test -s '${base}'
    units='${base.guestToplevel}/etc/systemd/system'
    test "$(readlink -f "$units/nix-daemon.service")" = '${engine}/lib/systemd/system/nix-daemon.service'
    test "$(readlink -f "$units/nix-daemon.socket")" = '${engine}/lib/systemd/system/nix-daemon.socket'
    test -L "$units/nix-daemon@.service"
    test "$(readlink -f "$units/nix-daemon@.service")" = /dev/null
    grep -Fx 'Requires=guest-store-registration.service' "$units/nix-daemon.service.d/overrides.conf"
    grep -Fx 'Requires=guest-store-registration.service' "$units/nix-daemon.socket.d/overrides.conf"
    grep -Fx 'Environment="NIX_SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"' \
      "$units/nix-daemon.service.d/overrides.conf"
    mkdir -p "$out"
    printf '%s\n' '${base}' > "$out/image"
    sha256sum "$units/nix-daemon.service" "$units/nix-daemon.socket" > "$out/units.sha256"
  '';
}
