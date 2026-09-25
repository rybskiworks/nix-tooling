{ pkgs, guest }:
let
  file = pkgs.writeText "external-regular-fixture" "external fixture\n";
  payload = pkgs.runCommand "external-directory-fixture" { } ''
    mkdir -p "$out/share"
    ln -s ${file} "$out/share/external-file"
  '';
  export = guest.mkClosureExport {
    inherit pkgs;
    name = "external-fixture";
    roots = [ payload ];
  };
  # Tiny synthetic parent for archive mechanics, not a bootable NixOS guest.
  empty = guest.mkImage {
    inherit pkgs;
    name = "external-empty-parent";
    tag = "test";
    maxLayers = 3;
  };
  base = empty.overrideAttrs (old: {
    passthru = (old.passthru or { }) // {
      guestInit = {
        executable = "/fixture";
      };
      guestSystem = { };
      guestToplevel = pkgs.emptyDirectory;
      guestRegistrationNames = [ "base" ];
      guestLayerBudget = 3;
      guestStoreLayerConfigs = [ empty.stream.conf ];
      guestExternalClosures = [ export ];
    };
  });
  leaf = guest.mkNixosLayer {
    inherit pkgs base;
    name = "external-leaf-fixture";
    tag = "test";
    registrationName = "example";
    contents = [ payload ];
    # Retain the directory root itself: symlinkJoin otherwise resolves its
    # only child directly to the regular-file output.
    extraCommands = ''
      printf '%s\n' '${payload}' > external-root
    '';
    maxLayers = 5;
  };
  control = guest.mkNixosLayer {
    inherit pkgs;
    base = base.overrideAttrs (old: {
      passthru = old.passthru // {
        guestExternalClosures = [ ];
      };
    });
    name = "embedded-leaf-control";
    tag = "test";
    registrationName = "control";
    contents = [ payload ];
    extraCommands = ''
      printf '%s\n' '${payload}' > external-root
    '';
    maxLayers = 5;
  };
  invalidRoots = builtins.tryEval (
    guest.mkClosureExport {
      inherit pkgs;
      name = "invalid";
      roots = [ ];
    }
  );
  invalidName = builtins.tryEval (
    guest.mkClosureExport {
      inherit pkgs;
      name = "../invalid";
      roots = [ payload ];
    }
  );
  spec = pkgs.writeText "external-image-fixture.json" (
    builtins.toJSON {
      archive = toString leaf;
      control = toString control;
      export = toString export;
      registration = "${leaf.guestRegistration.closure}/registration";
    }
  );
in
assert !invalidRoots.success && !invalidName.success;
assert leaf.guestExternalClosures == [ export ];
assert control.guestExternalClosures == [ ];
assert
  leaf.guestRegistration.roots == [
    leaf.guestRegistration.payload
    leaf.guestRegistration.runtimeConfig
  ];
pkgs.runCommand "external-closure-image-check"
  {
    nativeBuildInputs = [ pkgs.python3 ];
    exportReferencesGraph = [
      "retained-closure"
      export
    ];
  }
  ''
    mkdir -p "$out"
    export CLOSURE_EXPORT_SCRIPT=${../../lib/guest/closure-export.py}
    python3 -B ${./test_closure_export.py} -v
    python3 -B ${./check_external_image.py} ${spec} retained-closure > "$out/result.json"
  ''
