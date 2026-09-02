#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
canonical="$(cd "$script_dir/.." && pwd)/share/tombi-format.toml"

if [ ! -f "$canonical" ]; then
  echo "error: canonical not found at $canonical" >&2
  exit 1
fi

if [ $# -eq 0 ]; then
  echo "usage: $0 <tombi.toml> [...]" >&2
  exit 1
fi

extract_rules() {
  local file="$1"
  awk '
    BEGIN { in_format=0; in_lint=0 }
    /^[[:space:]]*toml-version[[:space:]]*=/ {
      sub(/#.*/, "", $0)
      gsub(/^[ \t]+|[ \t]+$/, "", $0)
      if ($0 != "") print $0
      next
    }
    /^\[format\.rules\]/ { in_format=1; in_lint=0; next }
    /^\[lint\.rules\]/ { in_format=0; in_lint=1; next }
    /^\[/ { in_format=0; in_lint=0; next }
    (in_format || in_lint) {
      sub(/#.*/, "", $0)
      gsub(/^[ \t]+|[ \t]+$/, "", $0)
      if ($0 != "") print $0
    }
  ' "$file" | sed -E 's/[[:space:]]*=[[:space:]]*/=/' | sort
}

canonical_tmp="$(mktemp)"
trap 'rm -f "$canonical_tmp"' EXIT
extract_rules "$canonical" > "$canonical_tmp"

failed=0

for file in "$@"; do
  if [ ! -f "$file" ]; then
    echo "error: file not found: $file" >&2
    failed=1
    continue
  fi
  tmp="$(mktemp)"
  extract_rules "$file" > "$tmp"
  if ! diff -u "$canonical_tmp" "$tmp" > "$tmp.diff" 2>&1; then
    echo "drift: $file differs from canonical $canonical" >&2
    cat "$tmp.diff" >&2
    failed=1
  else
    echo "ok: $file matches canonical"
  fi
  rm -f "$tmp" "$tmp.diff"
done

if [ "$failed" -ne 0 ]; then
  exit 1
fi
