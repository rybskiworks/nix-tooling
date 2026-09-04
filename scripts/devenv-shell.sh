#!/usr/bin/env bash
# devenv-shell.sh — interactive entry into this repo's devenv shell.
#
# Bare `nix develop` cannot resolve devenv.root under PURE evaluation
# (builtins.getEnv "PWD" is ""), and the old flake-side store-path fallback
# put devenv's dotfile/state into a read-only /nix/store copy. The
# devenv-native fix: override the flake's `devenv-root` placeholder input
# with a file holding this worktree's abs path — devenv's auto-imported
# readDevenvRoot module then sets devenv.root to the worktree (dotfile/state
# land in the gitignored in-tree .devenv/). Stable content = worktree path;
# --override-input never touches flake.lock.
#
# Usage:
#   ./scripts/devenv-shell.sh              # interactive shell
#   ./scripts/devenv-shell.sh -c <cmd>     # one-shot command in the shell
set -euo pipefail

_repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$_repo_root"

_devenv_root_dir="$HOME/.cache/workestrate/devenv-root"
mkdir -p "$_devenv_root_dir"
_devenv_root_file="$_devenv_root_dir/$(printf '%s' "$_repo_root" | sha256sum | cut -c1-12)"
printf '%s' "$_repo_root" > "$_devenv_root_file"

exec nix develop --override-input devenv-root "file+file://$_devenv_root_file" "$@"
