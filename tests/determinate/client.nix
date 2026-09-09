{
  pkgs,
  determinate,
}:
let
  inherit (pkgs) lib;
  engine = determinate.inputs.nix.packages.${pkgs.stdenv.hostPlatform.system}.default;
  module = import ../../devenvModules/determinate.nix determinate;
  # Reject accidental service, installer, environment or shell-entry side effects.
  evaluated = lib.evalModules {
    specialArgs = { inherit pkgs; };
    modules = [
      {
        options.packages = lib.mkOption {
          type = lib.types.listOf lib.types.package;
          default = [ ];
        };
      }
      module
    ];
  };
  selected = evaluated.config.packages;
in
assert builtins.length selected == 1;
assert (builtins.head selected).drvPath == engine.drvPath;
assert (builtins.head selected).outPath == (lib.getBin engine).outPath;
assert (lib.getDev (builtins.head selected)).outPath == (lib.getBin engine).outPath;
assert engine.drvPath != pkgs.nix.drvPath;
pkgs.runCommand "determinate-client-check" { nativeBuildInputs = selected; } ''
  export HOME="$TMPDIR/home" XDG_CONFIG_HOME="$TMPDIR/config"
  mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$out"
  test "$(command -v nix)" = '${engine}/bin/nix'
  nix --version > "$out/version"
  grep -F 'Determinate Nix ${engine.version}' "$out/version"
  printf '%s\n' '${engine}' > "$out/client"
''
