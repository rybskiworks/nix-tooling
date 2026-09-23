{
  pkgs,
  nixpkgs,
  package,
}:
let
  guest = import ../../lib/guest { inherit nixpkgs; };
  evaluated =
    extra:
    (guest.mkNixosSystem {
      inherit pkgs;
      stateVersion = "26.05";
      modules = [ ../../nixosModules/git-attribution.nix ] ++ extra;
    }).config;
  inert = evaluated [ ];
  configured = evaluated [
    {
      programs.gitAttribution = {
        enable = true;
        coAuthors = [ "Pat Example <pat@example.org>" ];
        allowedCoAuthors = [ "Sam Example <sam@example.org>" ];
      };
      programs.git.config = {
        user.name = "Existing author";
        user.email = "author@example.org";
        commit.gpgsign = true;
      };
    }
  ];
  customTemplate = evaluated [
    {
      programs.gitAttribution = {
        enable = true;
        coAuthors = [ "Pat Example <pat@example.org>" ];
        installTemplate = false;
      };
      programs.git.config = {
        init.templateDir = "/custom/template";
        core.hooksPath = "/custom/hooks";
      };
    }
  ];
  missingAuthors = evaluated [ { programs.gitAttribution.enable = true; } ];
  invalidAuthors = evaluated [
    {
      programs.gitAttribution = {
        enable = true;
        coAuthors = [ "Bad\nName <bad@example.org>" ];
      };
    }
  ];
  generatedConfig = pkgs.writeText "git-attribution-test-config" configured.environment.etc.gitconfig.text;
in
assert !inert.programs.gitAttribution.enable;
assert !(builtins.elem package inert.environment.systemPackages);
assert configured.programs.git.enable;
assert configured.programs.gitAttribution.strict;
assert builtins.all (entry: entry.assertion) configured.assertions;
assert !(builtins.all (entry: entry.assertion) missingAuthors.assertions);
assert
  !(builtins.tryEval (builtins.deepSeq invalidAuthors.programs.gitAttribution.coAuthors true))
  .success;
pkgs.runCommand "git-attribution-check"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.gitMinimal
      package
    ];
    GIT_ATTRIBUTION_TEST_COMMAND = "${package}/bin/git-attribution";
    GIT_ATTRIBUTION_SHELL = pkgs.runtimeShell;
  }
  ''
    export HOME="$TMPDIR/home" XDG_CONFIG_HOME="$TMPDIR/config"
    export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
    mkdir -p "$HOME" "$XDG_CONFIG_HOME"
    python3 ${./test_attribution.py}

    # Read the emitted NixOS config with Git, then use its actual clone template.
    test "$(git config --file ${generatedConfig} --get-all attribution.coAuthor)" = 'Pat Example <pat@example.org>'
    test "$(git config --file ${generatedConfig} --type=bool attribution.strict)" = true
    test "$(git config --file ${generatedConfig} --get-all attribution.allowedCoAuthor)" = 'Sam Example <sam@example.org>'
    test "$(git config --file ${generatedConfig} user.name)" = 'Existing author'
    test "$(git config --file ${generatedConfig} commit.gpgsign)" = true
    test -z "$(git config --file ${generatedConfig} core.hooksPath || true)"
    export GIT_CONFIG_GLOBAL=${generatedConfig}
    git init -q --bare "$TMPDIR/origin"
    git clone -q "$TMPDIR/origin" "$TMPDIR/clone"
    cd "$TMPDIR/clone"
    git config commit.gpgsign false
    printf '%s\n' 'template fixture' > file
    git add file
    git commit -q -m 'test: inherit attribution through clone template'
    git log -1 --format=%B > message
    git-attribution check --file message
    git-attribution install
    test ! -e .git/hooks/prepare-commit-msg.git-attribution-original
    test ! -e .git/hooks/commit-msg.git-attribution-original
    git commit -q --amend --no-edit
    git log -1 --format=%B > message
    git-attribution check --file message

    cat > "$TMPDIR/custom-config" <<'EOF'
    ${customTemplate.environment.etc.gitconfig.text}
    EOF
    test "$(git config --file "$TMPDIR/custom-config" core.hooksPath)" = /custom/hooks
    test "$(git config --file "$TMPDIR/custom-config" init.templateDir)" = /custom/template
    mkdir -p "$out"
  ''
