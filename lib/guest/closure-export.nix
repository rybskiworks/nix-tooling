{
  pkgs,
  name,
  roots,
}:
let
  inherit (pkgs) lib;
  validRoot = root: builtins.isPath root || lib.isDerivation root;
  closure = pkgs.closureInfo { rootPaths = roots; };
  rootsFile = pkgs.writeText "${name}-closure-roots" (lib.concatMapStrings (root: "${root}\n") roots);
in
assert lib.assertMsg (
  builtins.isString name && builtins.match "[a-z][a-z0-9-]*" name != null
) "guest: closure export name must be a lowercase identifier";
assert lib.assertMsg (
  builtins.isList roots && roots != [ ] && builtins.length roots <= 64 && builtins.all validRoot roots
) "guest: closure export roots must be a nonempty bounded list of derivations or paths";
assert lib.assertMsg (
  builtins.length (lib.unique (map toString roots)) == builtins.length roots
) "guest: closure export roots must be unique";
pkgs.runCommand "${name}-closure-export"
  {
    passthru.guestClosureExport = {
      schemaVersion = 1;
      inherit roots closure;
    };
  }
  ''
    ${pkgs.buildPackages.python3}/bin/python3 -B ${./closure-export.py} \
      ${closure} ${rootsFile} "$out"
  ''
