{ pkgs, nixpkgs }:
let
  inherit (pkgs) lib;
  guest = import ../../lib/guest {
    nixpkgs = {
      lib = lib // {
        nixosSystem = _: throw "the supplier evaluator must not be used";
      };
    };
  };
  consumerPkgs = pkgs.extend (_final: _prev: { guestPackageSetMarker = "consumer-package-set"; });
  args = {
    pkgs = consumerPkgs;
    stateVersion = "26.05";
    modules = [
      ({ pkgs, ... }: { environment.variables.CONSUMER_PACKAGES = pkgs.guestPackageSetMarker; })
    ];
  };
  evaluator =
    config:
    nixpkgs.lib.nixosSystem (
      config
      // {
        modules = config.modules ++ [ { system.nixos.label = "consumer-evaluator"; } ];
      }
    );
  source = nixpkgs // {
    lib = nixpkgs.lib // {
      nixosSystem = evaluator;
    };
  };
  explicitEvaluator = guest.mkNixosSystem (args // { nixosSystem = evaluator; });
  explicitSource = guest.mkNixosSystem (args // { nixpkgsSource = source; });
  rawSource = guest.mkNixosSystem (args // { nixpkgsSource = pkgs.path; });
  imageArgs = args // {
    name = "consumer-evaluator-test";
    modules = args.modules ++ [ (import ../../nixosModules/lix-guest.nix nixpkgs) ];
  };
  evaluatorImage = guest.mkNixosImage (imageArgs // { nixosSystem = evaluator; });
  sourceImage = guest.mkNixosImage (imageArgs // { nixpkgsSource = source; });
  rejects = override: !(builtins.tryEval (guest.mkNixosSystem (args // override))).success;
  assertions = {
    explicitEvaluatorSelected = explicitEvaluator.config.system.nixos.label == "consumer-evaluator";
    explicitSourceSelected = explicitSource.config.system.nixos.label == "consumer-evaluator";
    callerPackagesRetained =
      lib.all (system: system.config.environment.variables.CONSUMER_PACKAGES == "consumer-package-set")
        [
          explicitEvaluator
          explicitSource
          rawSource
          evaluatorImage.guestSystem
          sourceImage.guestSystem
        ];
    rawSourceSelected = rawSource.config.system.stateVersion == "26.05";
    imageEvaluatorForwarded =
      evaluatorImage.guestSystem.config.system.nixos.label == "consumer-evaluator";
    imageSourceForwarded = sourceImage.guestSystem.config.system.nixos.label == "consumer-evaluator";
    existingRuntimeAdapterRetained = evaluatorImage.guestInit.env.container == "microsandbox";
    invalidEvaluatorRejected = rejects { nixosSystem = { }; };
    invalidSourceRejected = rejects { nixpkgsSource = { }; };
    sourceUrlRejected = rejects { nixpkgsSource = "github:NixOS/nixpkgs"; };
    ambiguousSelectionRejected = rejects {
      nixosSystem = evaluator;
      nixpkgsSource = source;
    };
  };
in
{
  inherit assertions;
  contract =
    assert lib.assertMsg (builtins.all (value: value) (builtins.attrValues assertions)) (
      "Guest evaluator contract failed: "
      + lib.concatStringsSep ", " (builtins.attrNames (lib.filterAttrs (_name: value: !value) assertions))
    );
    pkgs.writeText "guest-evaluator-contract.json" (builtins.toJSON assertions);
}
