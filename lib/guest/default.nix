{ nixpkgs }:
let
  require =
    condition: message:
    assert condition || throw "guest: ${message}";
    true;
  isContent = value: builtins.isPath value || nixpkgs.lib.isDerivation value;
  isPkgs = pkgs: builtins.isAttrs pkgs && pkgs ? stdenv && pkgs ? dockerTools;
in
rec {
  inherit (import ./nixos-image.nix { inherit mkImage mkNixosSystem; }) mkNixosImage mkNixosLayer;

  # Keep dockerTools' archive, stream and layer semantics; no implicit guest tools.
  mkImage =
    {
      pkgs,
      name,
      tag ? null,
      contents ? [ ],
      rootfs ? null,
      config ? { },
      fromImage ? null,
      maxLayers ? 100,
      extraCommands ? "",
    }:
    assert require (isPkgs pkgs) "pkgs must be an explicit package set";
    assert require pkgs.stdenv.hostPlatform.isLinux "images require a Linux package set";
    assert require (builtins.isString name && name != "") "name must be a non-empty string";
    assert require (
      tag == null || (builtins.isString tag && tag != "")
    ) "tag must be null or a non-empty string";
    assert require (
      builtins.isList contents && builtins.all isContent contents
    ) "contents must be a list of derivations or paths";
    assert require (rootfs == null || isContent rootfs) "rootfs must be null, a derivation or a path";
    assert require (
      fromImage == null || isContent fromImage
    ) "fromImage must be null, an archive derivation or a path";
    assert require (builtins.isAttrs config) "config must be an OCI configuration attribute set";
    assert require (builtins.isInt maxLayers && maxLayers > 1) "maxLayers must be an integer above one";
    assert require (builtins.isString extraCommands) "extraCommands must be a string";
    pkgs.dockerTools.buildLayeredImage {
      inherit
        name
        tag
        config
        fromImage
        maxLayers
        extraCommands
        ;
      contents = contents ++ pkgs.lib.optional (rootfs != null) rootfs;
    };

  # Evaluation only: callers separately choose image assembly and activation.
  mkNixosSystem =
    {
      pkgs,
      stateVersion,
      modules ? [ ],
    }:
    assert require (isPkgs pkgs) "pkgs must be an explicit package set";
    assert require pkgs.stdenv.hostPlatform.isLinux "NixOS guests require a Linux package set";
    assert require (
      builtins.isString stateVersion && builtins.match "[0-9]{2}[.][0-9]{2}" stateVersion != null
    ) "stateVersion must be an explicit YY.MM string";
    assert require (builtins.isList modules) "modules must be a list";
    nixpkgs.lib.nixosSystem {
      modules = [
        ../../nixosModules/guest-base.nix
        {
          # Overlays/config already belong to the supplied package set.
          nixpkgs.pkgs = pkgs;
          system.stateVersion = stateVersion;
        }
      ]
      ++ modules;
    };
}
