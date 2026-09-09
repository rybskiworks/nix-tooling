{
  description = "nix-tooling — shared Nix dev tooling (devenv modules, treefmt, git-hooks, tombi, fenix toolchain)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/a799d3e3886da994fa307f817a6bc705ae538eeb";

    flake-parts = {
      url = "github:hercules-ci/flake-parts/9d0d87172c374f89da73c1cfe6d81ae62feac1f1";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };

    devenv = {
      url = "github:cachix/devenv/97135e80b6e432f41f84f72383e1b8147f33ef0c";
      inputs = {
        nixpkgs.follows = "nixpkgs";
        git-hooks.follows = "git-hooks";
        flake-parts.follows = "flake-parts";
      };
    };

    treefmt-nix = {
      url = "github:numtide/treefmt-nix/27b3b12a8e6375f28ebe122f07d230ca5459bbfa";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    git-hooks = {
      url = "github:cachix/git-hooks.nix/27555e2624241fb116b49095df4caaee85a25691";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Curated pinned toolchain: same rev as workestrate (rustc 1.97.1, 2026-07-16).
    # This is an OWNED pin — consumers should NOT `tooling.inputs.fenix.follows`-override it,
    # so the toolchain stays reproducible even when the consumer bumps nixpkgs.
    # Internally we DO set `fenix.inputs.nixpkgs.follows = "nixpkgs"` so fenix's deps
    # are built against the shared nixpkgs, but the fenix *rev* itself remains owned.
    fenix = {
      url = "github:nix-community/fenix/fa09e6473a0dfd673e6cb9a37741aec513b4bb2a";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    beads = {
      url = "github:gastownhall/beads/6c124203e771433a3550c348771a5b5e27fd3c21";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Preserve the official engine/Nixd package sets and their binary-cache identities.
    # Guest NixOS modules still receive the consumer's explicit nixpkgs.pkgs.
    determinate.url = "github:DeterminateSystems/determinate/cb76ac22754f6b36c008a3c39477c174a146dd6b";

    # Placeholder for pure evaluation: devenv's auto-imported readDevenvRoot
    # module sets devenv.root from builtins.readFile of this input when the
    # content is non-empty; /dev/null reads as "" (override inert, keeps
    # `nix flake show/check`-style eval of OTHER outputs working). Consumers
    # enter the shell via
    #   nix develop --override-input devenv-root "file+file://<rootfile>"
    # where <rootfile> is a FILE containing the worktree abs path (NOT the
    # directory — builtins.readFile requires a regular file; the older
    # "file+file://$PWD" form in docs was wrong). See scripts/devenv-shell.sh.
    # https://devenv.sh/guides/using-with-flakes/
    devenv-root = {
      url = "file+file:///dev/null";
      flake = false;
    };
  };

  # Decision log:
  # - nixpkgs rev a799d3e... is shared with workestrate for store-path deduplication (via follows in consumer).
  # - flake-parts 9d0d871, treefmt-nix 27b3b12, git-hooks 27555e, devenv 97135e are recent compatible revisions (validated 2026-09-01 together).
  # - fenix fa09e647 is pinned to workestrate's toolchain rev (rustc 1.97.1) for reproducibility; NOT following consumer nixpkgs in workestrate's
  #   `tooling.inputs.fenix.follows` ensures the toolchain is not silently upgraded by the consumer's nixpkgs bump.
  # - treefmt-nix has NO programs.tombi (128 programs checked); we provide custom formatter via treefmt.settings.formatter.tombi.
  # - git-hooks.nix has NO hooks.tombi; we provide custom hook `tombi-lint` + use treefmt hook for formatting. We set
  #   git-hooks.hooks.treefmt.settings.fail-on-change = false for auto-fix on commit (developer-friendly). CI can enforce
  #   `fail-on-change = true` via override or run `nix flake check` which uses `treefmt --fail-on-change` anyway.

  outputs =
    inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      imports = [
        inputs.devenv.flakeModule
        inputs.treefmt-nix.flakeModule
        inputs.git-hooks.flakeModule
      ];

      # tombiPkg is a pinned x86_64-linux-musl binary tarball (packages/tombi.nix meta.platforms)
      # consumed by every perSystem output, so other systems cannot evaluate/build;
      # widen only when per-system tombi artifacts are added.
      systems = [ "x86_64-linux" ];

      # Expose devenv modules as reusable flakes outputs.
      # Consumers: `devenv.shells.default.imports = [ inputs.tooling.devenvModules.base ... ]`
      flake = {
        devenvModules = {
          base = ./devenvModules/base.nix;
          nix = ./devenvModules/nix.nix;
          toml = ./devenvModules/toml.nix;
          rust = ./devenvModules/rust.nix;
          beads = import ./devenvModules/beads.nix inputs.beads;
        };
        lib.guest = import ./lib/guest { inherit (inputs) nixpkgs; };
        nixosModules.guestBase = ./nixosModules/guest-base.nix;
        nixosModules.determinateGuest = import ./nixosModules/determinate-guest.nix {
          inherit (inputs) determinate nixpkgs;
        };
      };

      perSystem =
        { system, ... }:
        let
          # pkgs with fenix overlay for rust toolchain (used both in perSystem formatter and in devenv modules via _module.args.pkgs).
          pkgsWithFenix = import inputs.nixpkgs {
            inherit system;
            overlays = [ inputs.fenix.overlays.default ];
            config.allowUnfree = true;
          };
          tombiPkg = pkgsWithFenix.callPackage ./packages/tombi.nix { };
          beadsPkg = inputs.beads.packages.${system}.bd;
          doltPkg = pkgsWithFenix.callPackage ./packages/dolt-bin.nix { };
          beadsServerChecks = import ./tests/beads-server {
            pkgs = pkgsWithFenix;
            beads = beadsPkg;
            dolt = doltPkg;
          };
          guestChecks = import ./tests/guest {
            pkgs = pkgsWithFenix;
            guest = import ./lib/guest { inherit (inputs) nixpkgs; };
          };
          determinateChecks = import ./tests/determinate {
            pkgs = pkgsWithFenix;
            inherit (inputs) determinate nixpkgs;
          };
        in
        {
          # Ensure all perSystem modules see the fenix overlay.
          _module.args.pkgs = pkgsWithFenix;

          # treefmt-nix: formatter for `nix fmt` and `nix flake check` (checks.treefmt)
          # Mirrors the devenvModules logic so `nix fmt` and `devenv` agree.
          treefmt.config = {
            projectRootFile = "flake.nix";
            programs = {
              nixfmt.enable = true;
              statix.enable = true;
              rustfmt = {
                enable = true;
                package = pkgsWithFenix.fenix.stable.rustfmt;
                edition = "2024";
              };
            };
            settings.formatter.tombi = {
              command = "${tombiPkg}/bin/tombi";
              options = [ "format" ];
              includes = [ "*.toml" ];
            };
          };

          # git-hooks.nix: pre-commit checks for `nix flake check`
          # Note: perSystem `pre-commit` is the git-hooks flakeModule option (not devenv's git-hooks).
          # It auto-creates `checks.pre-commit` and `pre-commit.devShell`.
          pre-commit = {
            check.enable = true;
            settings.hooks = {
              # Mirrors devenvModules/nix.nix + toml.nix but at the flake check level.
              nixfmt.enable = true;
              statix.enable = true;
              deadnix.enable = true;
              typos.enable = true;

              # Treefmt as a hook: runs treefmt wrapper (including tombi). Use fail-on-change = true for CI determinism.
              # The devenv shell uses fail-on-change = false (auto-fix), but `nix flake check` must be fail-closed.
              treefmt = {
                enable = true;
                settings.fail-on-change = true;
              };

              # Tombi lint (separate from format) — same as devenvModules/toml.nix but explicit for checks.
              tombi-lint = {
                enable = true;
                name = "tombi lint";
                entry = "${tombiPkg}/bin/tombi lint --error-on-warnings";
                files = "\\.toml$";
                pass_filenames = false;
              };

              # Rust hooks for flake checks — opt-in (mkDefault false in rust.nix); disabled here as nix-tooling has no .rs files.
              rustfmt.enable = false;
              clippy.enable = false;
            };
          };

          # Provide explicit checks alias for consumers that expect `checks.pre-commit` naming via git-hooks.
          # flakeModule already creates `checks.pre-commit`; no extra wiring needed.
          # Additional check: tombiCheck via filtered src (mirrors workestrate's lib.checks.tombiCheck)
          checks = {
            guest-contract = guestChecks.contract;
            determinate-contract = determinateChecks.contract;
            beads-server-contract = beadsServerChecks.contract;

            beads-version =
              pkgsWithFenix.runCommand "beads-version-check"
                {
                  nativeBuildInputs = [ beadsPkg ];
                  env.BD_DISABLE_METRICS = "1";
                  env.BD_DISABLE_EVENT_FLUSH = "1";
                }
                ''
                  export BEADS_DIR="$TMPDIR/no-database"
                  mkdir -p $out
                  bd version | tee $out/version
                  grep -F 'bd version 1.2.2' $out/version
                  bd --help > $out/help
                  test ! -e "$BEADS_DIR"
                '';

            beads-embedded =
              pkgsWithFenix.runCommand "beads-embedded-check"
                {
                  nativeBuildInputs = [
                    beadsPkg
                    pkgsWithFenix.gitMinimal
                    pkgsWithFenix.jq
                  ];
                  env.BD_DISABLE_METRICS = "1";
                  env.BD_DISABLE_EVENT_FLUSH = "1";
                }
                ''
                  export BEADS_DIR="$TMPDIR/project/.beads"
                  export DOLT_ROOT_PATH="$TMPDIR/dolt-global"
                  export XDG_CONFIG_HOME="$TMPDIR/xdg-config"
                  export XDG_CACHE_HOME="$TMPDIR/xdg-cache"
                  mkdir -p "$TMPDIR/project" $out
                  cd "$TMPDIR/project"
                  git init -q --initial-branch=main
                  git config user.name 'Beads package check'
                  git config user.email 'beads-check@example.invalid'
                  bd --sandbox init --prefix smoke --skip-hooks --skip-agents --non-interactive --quiet
                  bd --sandbox create 'Verify embedded storage' --type task --json > $out/created.json
                  bd --sandbox --readonly export --all -o $out/issues.jsonl
                  jq -se 'length == 1 and .[0].title == "Verify embedded storage"' $out/issues.jsonl
                  bd --sandbox --readonly ready --json > $out/ready.json
                  jq -e 'length == 1' $out/ready.json
                  test ! -e AGENTS.md
                  test ! -e .git/hooks/pre-commit
                  test ! -e .git/hooks/pre-push
                  test ! -e .git/hooks/prepare-commit-msg
                '';

            # The treefmt and pre-commit checks are auto-generated via the modules above.
            # We add a standalone tombi check that validates format+lint on the full repo with TOMBI_OFFLINE.
            tombiCheck =
              pkgsWithFenix.runCommand "tombi-check"
                {
                  nativeBuildInputs = [ tombiPkg ];
                }
                ''
                  mkdir -p $out
                  cd ${
                    pkgsWithFenix.lib.cleanSourceWith {
                      filter =
                        path: type:
                        type == "directory"
                        || pkgsWithFenix.lib.hasSuffix ".toml" (baseNameOf path)
                        || pkgsWithFenix.lib.hasSuffix ".schema.json" (baseNameOf path);
                      src = ./.;
                    }
                  }
                  export TOMBI_OFFLINE=true
                  tombi format --check
                  tombi lint --error-on-warnings
                  touch $out/ok
                '';

            # Manual drift gate wired into `nix flake check`; compares vendored tombi.toml
            # rules against canonical share/tombi-format.toml.
            tombi-sync =
              pkgsWithFenix.runCommand "tombi-sync-check"
                {
                  nativeBuildInputs = with pkgsWithFenix; [
                    bash
                    gawk
                    gnused
                    diffutils
                    coreutils
                  ];
                }
                ''
                  cd ${./.}
                  bash scripts/check-tombi-sync.sh tombi.toml
                  mkdir -p $out
                  touch $out/ok
                '';
          };

          # Formatter for `nix fmt` — treefmt wrapper.
          # This satisfies `nix fmt` running tombi + nixfmt + rustfmt.
          # formatter is auto-set by treefmt-nix.flakeModule, but explicit for clarity.
          # formatter = config.treefmt.build.wrapper; # set by module default

          # Packages
          packages = {
            tombi = tombiPkg;
            beads = beadsPkg;
            dolt-bin = doltPkg;
            default = tombiPkg;
            guest-minimal = guestChecks.image;
            determinate-nix = inputs.determinate.inputs.nix.packages.${system}.default;
            determinate-nixd = inputs.determinate.packages.${system}.default;
          };

          # Image realization stays explicit; ordinary tooling checks are small.
          legacyPackages = {
            guestChecks = {
              inherit (guestChecks) archive closure;
            };
            # The VM is opt-in and never part of an ordinary tooling flake check.
            determinateChecks = {
              inherit (determinateChecks) nixos;
            };
            beadsChecks = {
              inherit (beadsServerChecks) server;
            };
          };

          # Dogfooding devShell: uses its own devenvModules.
          devenv.shells.default = {
            # This repository does not publish shell/process container images.
            # Keep optional container dependencies out of its own output graph.
            containers = pkgsWithFenix.lib.mkForce { };

            # devenv.root is intentionally NOT set here: the auto-imported
            # readDevenvRoot module (inputs.devenv.flakeModule) sets it from
            # the `devenv-root` input placeholder (see inputs above;
            # --override-input never touches flake.lock). Entry points pass
            #   --override-input devenv-root "file+file://<rootfile>"
            # where <rootfile> holds the worktree abs path —
            # scripts/devenv-shell.sh writes
            # $HOME/.cache/workestrate/devenv-root/nix-tooling. Impure eval
            # falls back to devenv's mkDefault (getEnv PWD); pure eval WITHOUT
            # the override fails devenv's `devenv.root != ""` assertion by
            # design — the old store-path fallback put dotfile/state into a
            # read-only /nix/store copy. Dotfile/state keep devenv defaults:
            # dotfile = <root>/.devenv (gitignored in-tree), state follows.

            imports = [
              ./devenvModules/base.nix
              ./devenvModules/nix.nix
              ./devenvModules/toml.nix
              ./devenvModules/rust.nix
            ];

            # Add tombi explicitly to env (redundant via toml module but ensures visibility)
            packages = [ tombiPkg ];

            # Shell fundamentals
            enterShell = ''
              echo "nix-tooling dev shell (treefmt + typos + tombi + nixfmt + rust)"
              echo "tombi: $(tombi --version 2>/dev/null || echo 'tombi not found')"
            '';

            # Git root for treefmt wrapper (devenv's treefmt uses DEVENV_ROOT)
            # No custom config needed; devenv auto-detects.
          };
        };
    };
}
