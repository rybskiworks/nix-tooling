{
  config,
  lib,
  pkgs,
  ...
}:
let
  bundle = "/etc/ssl/certs/ca-certificates.crt";
  registration = pkgs.writeShellApplication {
    name = "register-guest-store";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.util-linux
      config.nix.package
    ];
    text = builtins.readFile ../lib/guest/register-store.sh;
  };
  requiresRegistration = {
    requires = [ "guest-store-registration.service" ];
    after = [ "guest-store-registration.service" ];
  };
in
{
  assertions = [
    {
      assertion = config.boot.isContainer && !config.boot.kernel.enable;
      message = "Microsandbox supplies the guest kernel; use NixOS container userspace.";
    }
    {
      assertion = config.nix.enable;
      message = "The registered guest image requires an explicit Nix daemon profile.";
    }
  ];

  # Agentd configures the interface and these files before handing off PID1.
  # Keep NixOS activation for users, NSS and units, without replacing that state.
  networking = {
    hostName = lib.mkForce "";
    useDHCP = false;
    useNetworkd = false;
    useHostResolvConf = false;
    dhcpcd.enable = false;
    networkmanager.enable = false;
    resolvconf.enable = false;
    firewall.enable = false;
  };
  services.resolved.enable = false;
  # Require the producer to mount volatile /run before creating runtime files.
  # Otherwise systemd would hide them under a fresh tmpfs during startup.
  boot.postBootCommands = lib.mkBefore ''
    if test "$(${pkgs.util-linux}/bin/findmnt --noheadings --output FSTYPE --mountpoint /run)" != tmpfs; then
      echo 'The guest runtime must mount /run as tmpfs before init handoff.' >&2
      exit 1
    fi
  '';
  environment.etc = lib.genAttrs [ "hosts" "hostname" "resolv.conf" ] (_: {
    enable = false;
  });
  security.pki.installCACerts = false;
  environment.sessionVariables = {
    SSL_CERT_FILE = bundle;
    NIX_SSL_CERT_FILE = bundle;
    CURL_CA_BUNDLE = bundle;
  };
  systemd = {
    network.enable = false;
    services = {
      nix-daemon = requiresRegistration // {
        environment = {
          CURL_CA_BUNDLE = lib.mkForce bundle;
          NIX_SSL_CERT_FILE = bundle;
        };
      };
      guest-store-registration = {
        description = "Register and root all immutable guest image payloads";
        requiredBy = [ "sysinit.target" ];
        before = [ "sysinit.target" ];
        after = [ "local-fs.target" ];
        unitConfig.DefaultDependencies = false;
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          ExecStart = "${registration}/bin/register-guest-store";
          TimeoutStartSec = 120;
        };
      };
    };
    sockets = {
      nix-daemon = requiresRegistration;
      determinate-nixd = lib.mkIf (config.determinate.enable or false) requiresRegistration;
    };
    targets.guest-store-ready = {
      description = "Guest image closure is registered in its own Nix store";
      wantedBy = [ "multi-user.target" ];
      requires = [ "guest-store-registration.service" ];
      after = [ "guest-store-registration.service" ];
    };
  };
}
