# base.nix — shared shell fundamentals via devenv.
#
# Provides:
# - core packages (git, jq)
# - typos spell-check via git-hooks
# - treefmt integration baseline (enabled, projectRootFile handling)
# - git-hooks.nix installationScript disabled (devenv's install task still installs the shim at entry — wanted)
# - shell entry never lint-gates: git-hooks:run and treefmt:run are cut out of the entry task closure
#
# This is a devenv module: `function { pkgs, lib, config, ... }: { ... }`
# It is imported via `devenv.shells.default.imports = [ inputs.tooling.devenvModules.base ]`
# from both nix-tooling (dogfooding) and consumers like workestrate.
#
# NOTE: git-hooks.enable is auto-enabled when any hook is enabled (devenv checks
# `anyHookEnabled`). Explicit `git-hooks.enable = true` is not required here.
{
  pkgs,
  lib,
  config,
  ...
}:
{
  packages = with pkgs; [
    git
    jq
  ];

  # Disable git-hooks.nix's installer so entering a devenv shell no longer
  # mutates `.git/hooks`.
  #
  # Previously, entering any devenv shell that imports these modules ran
  # git-hooks.nix's installer, which MOVED the tracked pure-sh fallback
  # (`.git/hooks/pre-commit.sh`) to `.git/hooks/pre-commit.legacy` and wrote a
  # store-path'd generated hook in its place. That generated hook dangles after
  # nix GC and drops the secret-material gate the pure-sh fallback enforces —
  # so commits outside a live devshell would either hard-fail or silently lose
  # the secret guard.
  #
  # Setting `install.enable = false` stops the `.git/hooks` mutation while
  # keeping the hook definitions fully evaluated and usable: they remain
  # available to `nix flake check` (via the separate perSystem `pre-commit`
  # namespace in flake.nix, which is unaffected by this devenv-side knob) and
  # to devenv's task system.
  #
  # `mkDefault` so a consumer that genuinely wants installer-managed hooks may
  # deliberately re-enable via `git-hooks.install.enable = lib.mkForce true;`.
  #
  # NOTE (devenv 97135e80): this knob gates only git-hooks.nix's own
  # installationScript. devenv's `devenv:git-hooks:install` TASK is defined
  # unconditionally and still installs the hook shim at shell entry — that
  # install is WANTED (it is cheap and idempotent); only the entry-time
  # lint RUN was the bug (fixed below).
  git-hooks.install.enable = lib.mkDefault false;

  # Typos spell-checker: shared baseline. Consumers can override
  # `git-hooks.hooks.typos.settings.*` or disable via `git-hooks.hooks.typos.enable = false`.
  git-hooks.hooks.typos = {
    enable = lib.mkDefault true;
    # Use default typos settings; consumers may add `settings.config` overrides.
  };

  # Treefmt integration baseline (devenv's treefmt, not perSystem.treefmt).
  # Actual formatters are enabled in the more specific nix/toml/rust modules.
  # `treefmt.enable` makes `treefmt` available as a package and wires
  # `git-hooks.hooks.treefmt.package` to the treefmt wrapper (via devenv's
  # treefmt integration). `projectRootFile` is auto-set to "" (treeRoot)
  # by devenv; override if a custom file is needed.
  treefmt.enable = lib.mkDefault true;

  # Ensure devenv's treefmt wrapper is used for the treefmt git hook.
  # This is the default via devenv's treefmt integration, but we keep it explicit.
  # Individual modules (nix, toml, rust) populate `treefmt.config.*`.

  # ---------------------------------------------------------------------
  # Shell entry must never lint-gate (root-cause fix).
  #
  # Pinned devenv 97135e80 runs entry tasks via
  #   devenv-tasks run devenv:enterShell --mode all
  # (src/modules/tasks.nix; the bash-hook path, because devenv.cli.version
  # is null under the flakes integration — the Rust 2.0+ path schedules the
  # same roots). RunMode::All (devenv-tasks/src/tasks.rs `schedule()`)
  # includes the root's DEPENDENTS and their prerequisites:
  #
  #   devenv:git-hooks:install --before--> devenv:enterShell
  #   devenv:enterShell --after--> devenv:enterTest
  #   devenv:git-hooks:run --before--> devenv:enterTest
  #   devenv:git-hooks:run --after--> devenv:git-hooks:install
  #
  # so `devenv:git-hooks:run` (the FULL prek battery: treefmt, typos,
  # deadnix, statix, tombi-lint) executed on EVERY shell entry, and its
  # failure marked enterShell "Dependency failed" — every
  # `nix develop -c <cmd>` exited 1.
  #
  # There is no upstream run-on-entry knob at this rev. Cutting the
  # enterShell -> enterTest edge removes enterTest (and therefore
  # git-hooks:run) from the entry closure: entry runs only enterShell plus
  # its prerequisites (devenv:files, git-hooks:install, ...), none of which
  # lint. `devenv test` still runs the battery — git-hooks:run keeps its
  # `before = [ "devenv:enterTest" ]` wiring, and RunMode::All from the
  # enterTest root pulls it in as a prerequisite.
  tasks."devenv:enterTest".after = lib.mkForce [ ];

  # devenv's treefmt integration (src/modules/integrations/treefmt.nix)
  # registers `devenv:treefmt:run` with `before = [ "devenv:enterShell" ]`,
  # running plain `treefmt` — which AUTO-FORMATS the worktree — on every
  # shell entry. Move it to test time: entry stays lint/format-free; it
  # still runs under `devenv test` or manually via
  # `devenv tasks run devenv:treefmt:run`. Gated on treefmt.enable so a
  # consumer that disables treefmt gets no stub no-exec task.
  tasks."devenv:treefmt:run" = lib.mkIf config.treefmt.enable {
    before = lib.mkForce [ "devenv:enterTest" ];
  };
}
