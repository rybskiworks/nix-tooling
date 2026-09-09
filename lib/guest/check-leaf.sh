# Validate trusted caller payload layout before composing it with the base.
check_leaf_root() (
  set -euo pipefail
  local root=$1 protected ancestor target
  for protected in init nix/guest-registrations nix/var run etc/hosts etc/hostname etc/resolv.conf etc/ssl/certs/ca-certificates.crt; do
    ancestor=$protected
    while [[ "$ancestor" == */* ]]; do
      ancestor=${ancestor%/*}
      target="$root/$ancestor"
      if test -e "$target" || test -L "$target"; then
        if test ! -d "$target" || test -L "$target"; then
          echo "leaf payload replaces protected base ancestor: $ancestor" >&2
          exit 1
        fi
      fi
    done
    target="$root/$protected"
    if test -e "$target" || test -L "$target"; then
      echo "leaf payload replaces protected base path: $protected" >&2
      exit 1
    fi
  done
)
