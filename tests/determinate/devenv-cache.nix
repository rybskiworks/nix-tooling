{
  pkgs,
  nixpkgs,
  determinate,
  devenv,
}:
let
  inherit (pkgs) lib;
  endpoint = "https://devenv.cachix.org";
  publicKey = "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw=";
  select = nixConfig: import ../../lib/devenv-cache.nix { inherit lib nixConfig; };
  valid = {
    extra-substituters = "${endpoint} https://cachix.cachix.org";
    extra-trusted-public-keys = "${publicKey} cachix.cachix.org-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  };
  rejects = value: !(builtins.tryEval (builtins.deepSeq (select value) true)).success;
  guest = import ../../lib/guest { inherit nixpkgs; };
  evaluate =
    modules:
    (guest.mkNixosSystem {
      inherit pkgs;
      stateVersion = "26.05";
      modules = [
        (import ../../nixosModules/determinate-guest.nix { inherit determinate nixpkgs; })
      ]
      ++ modules;
    }).config;
  module = import ../../nixosModules/devenv-cache.nix devenv;
  base = evaluate [ ];
  enabled = evaluate [ module ];
  consumer = evaluate [
    module
    {
      nix.settings.substituters = [ "https://cache.example.invalid" ];
      nix.settings.trusted-public-keys = [
        "example.invalid-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
      ];
    }
  ];
  withoutCaches =
    value:
    builtins.removeAttrs value [
      "substituters"
      "trusted-public-keys"
    ];
  sort = lib.sort builtins.lessThan;
  assertions = {
    sourceDeclaration =
      select (import "${devenv}/flake.nix").nixConfig == {
        substituters = [ endpoint ];
        trusted-public-keys = [ publicKey ];
      };
    stringDeclaration = (select valid).trusted-public-keys == [ publicKey ];
    listDeclaration =
      select {
        extra-substituters = [ endpoint ];
        extra-trusted-public-keys = [ publicKey ];
      } == select valid;
    defaultCacheUnchanged = !builtins.elem endpoint base.nix.settings.substituters;
    defaultKeyUnchanged = !builtins.elem publicKey base.nix.settings.trusted-public-keys;
    onlySelectedCacheAdded =
      sort enabled.nix.settings.substituters == sort (base.nix.settings.substituters ++ [ endpoint ]);
    onlySelectedKeyAdded =
      sort enabled.nix.settings.trusted-public-keys
      == sort (base.nix.settings.trusted-public-keys ++ [ publicKey ]);
    consumerCachePreserved =
      sort consumer.nix.settings.substituters
      == sort (enabled.nix.settings.substituters ++ [ "https://cache.example.invalid" ]);
    consumerKeyPreserved =
      sort consumer.nix.settings.trusted-public-keys == sort (
        enabled.nix.settings.trusted-public-keys
        ++ [ "example.invalid-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" ]
      );
    otherSettingsUnchanged = withoutCaches base.nix.settings == withoutCaches enabled.nix.settings;
    sandboxRetained = enabled.nix.settings.sandbox && !enabled.nix.settings.sandbox-fallback;
    signaturesRetained = enabled.nix.settings.require-sigs;
    noAutomaticFlakeTrust = !enabled.nix.settings.accept-flake-config;
    rootOnlyTrust = lib.unique enabled.nix.settings.trusted-users == [ "root" ];
    noExtraTrustedSubstituters = enabled.nix.settings.trusted-substituters == [ ];
    engineUnchanged = enabled.nix.package.drvPath == base.nix.package.drvPath;
    daemonCommandUnchanged =
      enabled.systemd.services.nix-daemon.serviceConfig.ExecStart
      == base.systemd.services.nix-daemon.serviceConfig.ExecStart;
    daemonEnvironmentUnchanged =
      enabled.systemd.services.nix-daemon.environment == base.systemd.services.nix-daemon.environment;
    noExtraPackages = enabled.environment.systemPackages == base.environment.systemPackages;
    missingDeclarationRejected = rejects { };
    missingEndpointRejected = rejects (valid // { extra-substituters = "https://cachix.cachix.org"; });
    duplicateEndpointRejected = rejects (valid // { extra-substituters = "${endpoint} ${endpoint}"; });
    endpointTypeRejected = rejects (valid // { extra-substituters = 42; });
    endpointElementTypeRejected = rejects (
      valid
      // {
        extra-substituters = [
          endpoint
          42
        ];
      }
    );
    missingKeyRejected = rejects (builtins.removeAttrs valid [ "extra-trusted-public-keys" ]);
    duplicateKeyRejected = rejects (
      valid // { extra-trusted-public-keys = "${publicKey} ${publicKey}"; }
    );
    wrongKeyPrefixRejected = rejects (
      valid
      // {
        extra-trusted-public-keys = "cachix.cachix.org-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
      }
    );
    malformedKeyRejected = rejects (
      valid // { extra-trusted-public-keys = "devenv.cachix.org-1:invalid"; }
    );
    keyTypeRejected = rejects (valid // { extra-trusted-public-keys = { }; });
    keyElementTypeRejected = rejects (
      valid
      // {
        extra-trusted-public-keys = [
          publicKey
          false
        ];
      }
    );
  };
in
assert lib.assertMsg (builtins.all (value: value) (builtins.attrValues assertions)) (
  "devenv guest cache contract failed: "
  + lib.concatStringsSep ", " (builtins.attrNames (lib.filterAttrs (_name: value: !value) assertions))
);
pkgs.writeText "devenv-guest-cache-contract.json" (builtins.toJSON assertions)
