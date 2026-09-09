{ pkgs, guest }:
let
  inherit (pkgs) lib;
  args = {
    inherit pkgs;
    name = "guest-minimal";
    tag = "test";
    contents = [
      pkgs.busybox
      pkgs.dockerTools.fakeNss
    ];
    config = {
      Cmd = [ "/bin/sh" ];
      WorkingDir = "/tmp";
      Env = [ "PATH=/bin" ];
    };
    extraCommands = ''
      mkdir -p tmp
      chmod 1777 tmp
    '';
  };
  image = guest.mkImage args;
  nativeImage = pkgs.dockerTools.buildLayeredImage (
    builtins.removeAttrs args [ "pkgs" ]
    // {
      fromImage = null;
      maxLayers = 100;
    }
  );
  childRoot = pkgs.writeTextDir "etc/guest-example" "child rootfs\n";
  childArgs = args // {
    name = "guest-child";
    fromImage = image;
    contents = [ ];
    rootfs = childRoot;
  };
  nativeChild = pkgs.dockerTools.buildLayeredImage (
    builtins.removeAttrs childArgs [
      "pkgs"
      "rootfs"
    ]
    // {
      contents = [ childRoot ];
      maxLayers = 100;
    }
  );
  rejects = override: !(builtins.tryEval (guest.mkImage (args // override))).success;
  markedPkgs = pkgs.extend (_final: _prev: { guestPackageSetMarker = "explicit-package-set"; });
  system = guest.mkNixosSystem {
    pkgs = markedPkgs;
    stateVersion = "26.05";
    modules = [
      (
        { pkgs, ... }:
        {
          system.nixos.label = pkgs.guestPackageSetMarker;
        }
      )
    ];
  };
  assertions = {
    archiveIdentity = image.drvPath == nativeImage.drvPath;
    streamIdentity = image.stream.drvPath == nativeImage.stream.drvPath;
    parentAndRootfsIdentity = (guest.mkImage childArgs).drvPath == nativeChild.drvPath;
    explicitPackageSet = system.config.system.nixos.label == "explicit-package-set";
    noImplicitEngine = !system.config.nix.enable;
    explicitStateVersion = system.config.system.stateVersion == "26.05";
    invalidName = rejects { name = ""; };
    invalidTag = rejects { tag = 1; };
    invalidContents = rejects { contents = pkgs.busybox; };
    invalidContentElement = rejects { contents = [ "ambient/path" ]; };
    invalidRootfs = rejects { rootfs = { }; };
    invalidConfig = rejects { config = [ ]; };
    invalidParent = rejects { fromImage = false; };
    invalidLayerCount = rejects { maxLayers = 1; };
    invalidExtraCommands = rejects { extraCommands = [ ]; };
    invalidPkgs = rejects { pkgs = null; };
    invalidStateVersion =
      !(builtins.tryEval (
        guest.mkNixosSystem {
          inherit pkgs;
          stateVersion = "latest";
        }
      )).success;
    invalidModules =
      !(builtins.tryEval (
        guest.mkNixosSystem {
          inherit pkgs;
          stateVersion = "26.05";
          modules = { };
        }
      )).success;
  };
  closure = pkgs.closureInfo { rootPaths = args.contents; };
in
{
  inherit image;
  contract =
    assert lib.assertMsg (builtins.all (value: value) (builtins.attrValues assertions)) (
      "guest contract failed: "
      + lib.concatStringsSep ", " (builtins.attrNames (lib.filterAttrs (_name: value: !value) assertions))
    );
    pkgs.writeText "guest-contract.json" (builtins.toJSON assertions);

  # These opt-in checks never enter a guest or load an image into a runtime.
  archive = pkgs.runCommand "guest-archive-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    mkdir -p "$out"
    python3 ${./check_archive.py} ${image} ${closure}/store-paths ${pkgs.dockerTools.fakeNss} > "$out/report.json"
  '';

  closure = pkgs.runCommand "guest-closure-check" { } ''
    if grep -E '/[^/]*-(nix-[0-9]|determinate|beads-|workestrate-|microsandbox-|codex-|prime-)' ${closure}/store-paths; then
      echo "unexpected guest engine, runtime or application in minimal closure" >&2
      exit 1
    fi
    mkdir -p "$out"
    cp ${closure}/store-paths "$out/store-paths"
  '';
}
