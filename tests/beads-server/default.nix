{
  pkgs,
  beads,
  dolt,
}:
let
  source = pkgs.lib.fileset.toSource {
    root = ./.;
    fileset = pkgs.lib.fileset.unions [
      ./contract.py
      ./test_contract.py
      ./server.py
    ];
  };
  python = pkgs.python3.withPackages (p: [ p.pymysql ]);
in
{
  contract =
    pkgs.runCommand "beads-server-contract-check"
      {
        nativeBuildInputs = [ pkgs.python3 ];
      }
      ''
        export PYTHONDONTWRITEBYTECODE=1
        python -m unittest discover -s ${source} -p test_contract.py -v
        mkdir -p "$out"
        touch "$out/passed"
      '';

  # Explicitly opt in: this starts a local SQL listener inside the build sandbox.
  server =
    pkgs.runCommand "beads-server-native-check"
      {
        nativeBuildInputs = [ python ];
      }
      ''
        export PYTHONDONTWRITEBYTECODE=1
        python ${source}/server.py \
          --bd ${beads}/bin/bd \
          --dolt ${dolt}/bin/dolt \
          --git ${pkgs.gitMinimal}/bin/git \
          --openssl ${pkgs.openssl}/bin/openssl \
          --scratch "$TMPDIR" --output "$out"
      '';
}
