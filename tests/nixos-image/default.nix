{
  pkgs,
  guest,
  base,
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
  cfg = base.guestSystem.config;
  assertions = {
    kernelDisabled = !cfg.boot.kernel.enable;
    containerUserspace = cfg.boot.isContainer;
    engineEnabled = cfg.nix.enable;
    determinateEnabled = cfg.determinate.enable;
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
in
assert builtins.all (value: value) (builtins.attrValues assertions);
{
  inherit base leaf assertions;
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
        python ${./test_registration.py} -v
        touch "$out/ok"
      '';
}
