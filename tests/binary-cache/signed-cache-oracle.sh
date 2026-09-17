#!/usr/bin/env bash
#
# Signed binary-cache oracle.
#
# Proves, offline and without touching the shared daemon, that a client can
# substitute an exact closure from a local file:// binary cache signed with a
# synthetic key, and that every weaker configuration is rejected: an unsigned
# entry, a wrong trusted public key, a tampered NAR, and an absent endpoint
# that must not fall back to a local build. It also shows what a read-only
# consumer can and cannot do with the same configuration.
#
# Roles exercised here:
#   producer/signer - a private chroot store that builds the reviewed payload
#                     plus a synthetic key pair owned by that role;
#   reader          - a fresh private chroot store per case that may only
#                     substitute, never build and never upload.
#
# Everything is created under one temporary root and removed on exit. No
# network endpoint, shared store path, account or credential is involved, and
# the synthetic secret key is neither printed nor stored outside that root.
#
# Usage: tests/binary-cache/signed-cache-oracle.sh
#   KEEP=1    keep the scratch root and print its path
#   NIX=...   override the nix binary (nix-store via NIX_STORE)
#
# Exit status is 0 only when every case passes.

set -euo pipefail

umask 077

# The shared daemon is deliberately out of scope: every store is selected
# explicitly with --store, and dropping an inherited NIX_REMOTE keeps a store
# operation from turning into a daemon request.
unset NIX_REMOTE

NIX=${NIX:-nix}
NIX_STORE=${NIX_STORE:-nix-store}
KEY_NAME=${KEY_NAME:-signed-cache-oracle-1}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PAYLOAD_EXPR="$here/payload.nix"
KEEP=${KEEP:-0}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/signed-cache-oracle.XXXXXX")
LOG="$WORK/oracle.log"
: > "$LOG"

cleanup() {
  if [ "$KEEP" = 1 ]; then
    printf 'scratch root kept at %s\n' "$WORK"
  else
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

# Everything the oracle prints goes through say(), which mirrors to the log.
# The log doubles as evidence: it is searched below for secret material.
say() { printf '%s\n' "$*" | tee -a "$LOG"; }

# run <cmd...>: execute, log the combined output, keep the command's status.
LAST_OUTPUT=
run() {
  local status
  local out
  set +e
  out=$("$@" 2>&1)
  status=$?
  set -e
  LAST_OUTPUT=$out
  printf '%s\n' "$out" >> "$LOG"
  printf '%s\n' "$out"
  return $status
}

PASSED=0
FAILED=0
pass() { PASSED=$((PASSED + 1)); say "  ok   $1"; }
fail() { FAILED=$((FAILED + 1)); say "  FAIL $1"; }

expect_success() { # expect_success <description> <cmd...>
  local desc=$1
  shift
  if run "$@"; then pass "$desc"; else fail "$desc (expected success)"; fi
}

expect_failure() { # expect_failure <description> <cmd...>
  local desc=$1
  shift
  if run "$@"; then fail "$desc (expected failure, but the command succeeded)"; else pass "$desc"; fi
}

assert_equal() { # assert_equal <description> <expected> <actual>
  local desc=$1 want=$2 got=$3
  if [ "$want" = "$got" ]; then pass "$desc"; else fail "$desc (want [$want], got [$got])"; fi
}

assert_not_empty() { # assert_not_empty <description> <value>
  local desc=$1 value=$2
  if [ -n "$value" ]; then pass "$desc"; else fail "$desc (value was empty)"; fi
}

assert_absent() { # assert_absent <description> <path>
  local desc=$1 path=$2
  if [ -e "$path" ]; then fail "$desc (unexpectedly present: $path)"; else pass "$desc"; fi
}

assert_contains() { # assert_contains <description> <needle> <file>
  local desc=$1 needle=$2 file=$3
  if grep -qF -- "$needle" "$file"; then pass "$desc"; else fail "$desc (no match for [$needle] in $file)"; fi
}

assert_not_contains() { # assert_not_contains <description> <needle> <file>
  local desc=$1 needle=$2 file=$3
  if grep -qF -- "$needle" "$file"; then fail "$desc (unexpected match in $file)"; else pass "$desc"; fi
}

assert_no_secret_in() { # assert_no_secret_in <description> <path>
  local desc=$1 path=$2
  if grep -rqF -- "$SECRET_KEY_BLOB" "$path"; then
    fail "$desc (secret key found under $path)"
  else
    pass "$desc"
  fi
}

assert_private_root() { # assert_private_root <description> <path>
  local desc=$1 path=$2
  case "$path" in
    "$WORK"/*) pass "$desc" ;;
    *) fail "$desc (path escapes the scratch root: $path)" ;;
  esac
}

# Count store entries whose name mentions the payload. Zero means the store
# never received the payload from the cache and never built it locally.
store_payload_entries() { # store_payload_entries <store-root>
  find "$1/nix/store" -maxdepth 1 -name '*signed-cache-oracle-payload*' 2>/dev/null | wc -l
}

nar_hash_of() { # nar_hash_of <store> <store-path>
  local out
  out=$("$NIX" path-info --store "$1" --json "$2" 2>>"$LOG") || return 1
  printf '%s' "$out" | grep -o '"narHash":"[^"]*"' | head -n1 || true
}

# -- reader configuration ---------------------------------------------------
# Every reader invocation is pinned so the oracle stays offline and cannot
# fall back to a local build:
#   substituters         the file:// cache under test only
#   trusted-public-keys  the producer's synthetic public key
#   require-sigs         refuse unsigned or unverifiable cache entries
#   builders ""          no remote builders
#   fallback false       never build locally when substitution fails
#   connect-timeout 5    bound the absent-endpoint case
TRUSTED_KEYS=
reader() { # reader <store> <cache-dir> <nix-store arguments...>
  local store=$1 cache=$2
  shift 2
  run "$NIX_STORE" --store "$store" \
    --option substituters "file://$cache" \
    --option trusted-public-keys "$TRUSTED_KEYS" \
    --option require-sigs true \
    --option builders "" \
    --option fallback false \
    --option connect-timeout 5 \
    "$@"
}

reader_case() { # reader_case <ok|fail> <description> <store> <cache> <args...>
  local expectation=$1 desc=$2 store=$3 cache=$4
  shift 4
  if reader "$store" "$cache" "$@"; then
    if [ "$expectation" = ok ]; then pass "$desc"; else fail "$desc (unexpectedly succeeded)"; fi
  else
    if [ "$expectation" = fail ]; then pass "$desc"; else fail "$desc (unexpectedly failed)"; fi
  fi
}

say "== signed binary-cache oracle"
say "   scratch root: $WORK"
say "   nix:          $("$NIX" --version 2>&1)"
say "   payload:      $PAYLOAD_EXPR"
say ""

# -- setup ------------------------------------------------------------------
say "== setup: synthetic keys, inside the scratch root only"
mkdir -p "$WORK/keys" "$WORK/stores"

SECRET_KEY="$WORK/keys/oracle.secret"
PUBLIC_KEY_FILE="$WORK/keys/oracle.public"
WRONG_SECRET_KEY="$WORK/keys/other.secret"
WRONG_PUBLIC_KEY_FILE="$WORK/keys/other.public"

if ! "$NIX" key generate-secret --key-name "$KEY_NAME" > "$SECRET_KEY" 2>>"$LOG"; then
  say "FATAL: could not generate the synthetic secret key"
  exit 1
fi
if ! "$NIX" key convert-secret-to-public < "$SECRET_KEY" > "$PUBLIC_KEY_FILE" 2>>"$LOG"; then
  say "FATAL: could not derive the synthetic public key"
  exit 1
fi
if ! "$NIX" key generate-secret --key-name untrusted-other-1 > "$WRONG_SECRET_KEY" 2>>"$LOG"; then
  say "FATAL: could not generate the second synthetic secret key"
  exit 1
fi
if ! "$NIX" key convert-secret-to-public < "$WRONG_SECRET_KEY" > "$WRONG_PUBLIC_KEY_FILE" 2>>"$LOG"; then
  say "FATAL: could not derive the second synthetic public key"
  exit 1
fi

PUBLIC_KEY=$(cat "$PUBLIC_KEY_FILE")
WRONG_PUBLIC_KEY=$(cat "$WRONG_PUBLIC_KEY_FILE")
SECRET_KEY_BLOB=$(cat "$SECRET_KEY")
TRUSTED_KEYS=$PUBLIC_KEY

assert_private_root "secret key lives inside the scratch root" "$SECRET_KEY"
assert_private_root "public key lives inside the scratch root" "$PUBLIC_KEY_FILE"
assert_equal "secret key file is owner-only" "600" "$(stat -c '%a' "$SECRET_KEY")"
say "   synthetic public key (the only key material printed): $PUBLIC_KEY"
say ""

PRODUCER="$WORK/stores/producer"
CACHE="$WORK/cache"

# -- producer and signer ----------------------------------------------------
say "== producer/signer: build the reviewed payload and publish it signed"
expect_success "payload builds client-side in a private chroot store" \
  "$NIX" build --store "$PRODUCER" --file "$PAYLOAD_EXPR" \
  --no-link --print-out-paths --option sandbox false \
  --option substituters "" --option builders ""
OUT_PATH=$(printf '%s\n' "$LAST_OUTPUT" | grep -E '^/nix/store/.+-signed-cache-oracle-payload$' | tail -n1)
if [ -z "$OUT_PATH" ]; then
  say "FATAL: could not determine the payload store path from:"
  say "$LAST_OUTPUT"
  exit 1
fi
say "   payload store path: $OUT_PATH"
assert_private_root "producer store is inside the scratch root" "$PRODUCER"

PRODUCER_FILE="$PRODUCER$OUT_PATH"
PRODUCER_SUM=$(sha256sum "$PRODUCER_FILE" | cut -d' ' -f1)
DRV_PATH=$("$NIX_STORE" --store "$PRODUCER" --query --deriver "$OUT_PATH" 2>>"$LOG" || true)
say "   payload content sha256: $PRODUCER_SUM"
say "   payload derivation:     ${DRV_PATH:-unknown}"
assert_equal "the producer copy executes and prints the marker" \
  "signed-cache-oracle-payload-v1" "$(sh "$PRODUCER_FILE")"

# Lix 2.94 signs a file:// cache only when the secret key is named in the
# store URL; the secret-key-files setting is not consulted on this path. The
# URL therefore carries the key *path*, while the key *material* is never
# printed or logged, which the hygiene cases below assert.
expect_success "copy to file:// signs the entry with the synthetic key" \
  "$NIX" copy --from "$PRODUCER" \
  --to "file://$CACHE?secret-key=$SECRET_KEY&compression=none" "$OUT_PATH"
CACHE_NARINFO=$(ls "$CACHE"/*.narinfo | head -n1)
assert_contains "cache narinfo carries the synthetic signature" "Sig: $KEY_NAME:" "$CACHE_NARINFO"
say "   cache contents: $(cd "$CACHE" && ls | tr '\n' ' ')"
say "   published narinfo:"
sed 's/^/     /' "$CACHE_NARINFO" | tee -a "$LOG"
say ""

# -- positive ---------------------------------------------------------------
say "== positive: a fresh store substitutes the signed payload"
READER1="$WORK/stores/reader-signed"
reader_case ok "fresh store substitutes the payload from the signed cache" \
  "$READER1" "$CACHE" --realise "$OUT_PATH"
READER1_FILE="$READER1$OUT_PATH"
assert_private_root "reader store is inside the scratch root" "$READER1"
assert_equal "substituted bytes equal the producer bytes" \
  "$PRODUCER_SUM" "$(sha256sum "$READER1_FILE" | cut -d' ' -f1)"
assert_equal "substituted copy executes and prints the marker" \
  "signed-cache-oracle-payload-v1" "$(sh "$READER1_FILE")"

PRODUCER_NAR=$(nar_hash_of "$PRODUCER" "$OUT_PATH" || true)
READER_NAR=$(nar_hash_of "$READER1" "$OUT_PATH" || true)
assert_not_empty "producer reports a nar hash" "$PRODUCER_NAR"
assert_equal "registered nar hash matches in both stores" "$PRODUCER_NAR" "$READER_NAR"
say ""

# -- negative: unsigned -----------------------------------------------------
say "== negative: an unsigned cache entry under require-sigs"
UNSIGNED="$WORK/cache-unsigned"
expect_success "copy without a secret key writes an unsigned narinfo" \
  "$NIX" copy --from "$PRODUCER" --to "file://$UNSIGNED?compression=none" "$OUT_PATH"
assert_not_contains "unsigned narinfo carries no signature" "Sig: " "$(ls "$UNSIGNED"/*.narinfo | head -n1)"
READER2="$WORK/stores/reader-unsigned"
reader_case fail "require-sigs rejects the unsigned entry" "$READER2" "$UNSIGNED" --realise "$OUT_PATH"
assert_equal "rejected case left no payload in its store" "0" "$(store_payload_entries "$READER2")"
say ""

# -- negative: wrong public key --------------------------------------------
say "== negative: a wrong trusted public key"
READER3="$WORK/stores/reader-wrong-key"
TRUSTED_KEYS=$WRONG_PUBLIC_KEY
reader_case fail "an untrusted public key rejects the signed entry" \
  "$READER3" "$CACHE" --realise "$OUT_PATH"
TRUSTED_KEYS=$PUBLIC_KEY
assert_equal "wrong-key case left no payload in its store" "0" "$(store_payload_entries "$READER3")"
say ""

# -- negative: tampered NAR -------------------------------------------------
say "== negative: a tampered NAR with an intact narinfo"
TAMPERED="$WORK/cache-tampered"
cp -a "$CACHE" "$TAMPERED"
CACHE_NAR_REL=$(cd "$CACHE" && ls nar/*.nar | head -n1)
CACHE_NAR="$CACHE/$CACHE_NAR_REL"
TAMPERED_NAR="$TAMPERED/$CACHE_NAR_REL"
TAMPERED_NARINFO="$TAMPERED/$(basename "$CACHE_NARINFO")"
NAR_BEFORE=$(sha256sum "$TAMPERED_NAR" | cut -d' ' -f1)
python3 -c 'import sys
path = sys.argv[1]
data = bytearray(open(path, "rb").read())
data[len(data) // 2] ^= 0xff
open(path, "wb").write(bytes(data))' "$TAMPERED_NAR"
NAR_AFTER=$(sha256sum "$TAMPERED_NAR" | cut -d' ' -f1)
assert_equal "tampering changed the NAR" "different" \
  "$([ "$NAR_BEFORE" = "$NAR_AFTER" ] && printf same || printf different)"
assert_equal "the narinfo of the tampered cache is byte-identical" \
  "$(sha256sum "$CACHE_NARINFO" | cut -d' ' -f1)" \
  "$(sha256sum "$TAMPERED_NARINFO" | cut -d' ' -f1)"
READER4="$WORK/stores/reader-tampered"
reader_case fail "the tampered NAR is rejected instead of substituted" \
  "$READER4" "$TAMPERED" --realise "$OUT_PATH"
assert_equal "tampered case left no payload in its store" "0" "$(store_payload_entries "$READER4")"
say ""

# -- negative: absent endpoint ---------------------------------------------
say "== negative: an absent endpoint fails and does not build locally"
ABSENT="$WORK/cache-absent"
assert_absent "the absent endpoint really does not exist" "$ABSENT"
READER5="$WORK/stores/reader-absent"
reader_case fail "substitution from an absent endpoint fails" \
  "$READER5" "$ABSENT" --realise "$OUT_PATH"
assert_equal "absent case left no payload in its store" "0" "$(store_payload_entries "$READER5")"
if [ -n "$DRV_PATH" ]; then
  assert_absent "absent case never received the derivation, so nothing can build" \
    "$READER5$DRV_PATH"
fi
say "   absent-endpoint store entries: $(ls "$READER5/nix/store" 2>/dev/null | wc -l)"
say "   fallback is disabled, so a failed substitution cannot become a local build"
say ""

# -- read-only consumer -----------------------------------------------------
say "== read-only consumer: a reader can mirror, but holds no signing credential"
assert_private_root "the published cache is inside the scratch root" "$CACHE"
# A reader invocation in this oracle is exactly the consumer side of the
# contract: a cache URL and a trusted public key. It carries no secret key, no
# account token and no upload destination, so a reader cannot mint an entry
# that other readers would trust. It can replay one it already substituted,
# signature included, which is a mirror, not a mint.
EXPORT="$WORK/cache-reader-export"
expect_success "a reader can export a substituted path locally" \
  "$NIX" copy --from "$READER1" --to "file://$EXPORT?compression=none" "$OUT_PATH"
EXPORT_NARINFO=$(ls "$EXPORT"/*.narinfo | head -n1)
assert_equal "the exported entry replays the producer signature" \
  "$(grep '^Sig:' "$CACHE_NARINFO" | sort)" "$(grep '^Sig:' "$EXPORT_NARINFO" | sort)"
assert_private_root "reader exports stay inside the scratch root" "$EXPORT"

# Holding some key is not enough: a second synthetic key signs its own cache
# entry correctly, and readers that do not trust it still refuse the entry.
UNTRUSTED_CACHE="$WORK/cache-untrusted-signer"
expect_success "a second synthetic key signs its own cache entry" \
  "$NIX" copy --from "$PRODUCER" \
  --to "file://$UNTRUSTED_CACHE?secret-key=$WRONG_SECRET_KEY&compression=none" "$OUT_PATH"
assert_contains "the untrusted cache carries a well-formed signature" \
  "Sig: untrusted-other-1:" "$(ls "$UNTRUSTED_CACHE"/*.narinfo | head -n1)"
reader_case fail "a signature from an untrusted key is refused" \
  "$WORK/stores/reader-untrusted-signer" "$UNTRUSTED_CACHE" --realise "$OUT_PATH"
say "   publishing new content would additionally require a secret key whose"
say "   public half every reader trusts, a writable endpoint or account token,"
say "   and operator authority to hold and use that key"
say ""

# -- hygiene ----------------------------------------------------------------
say "== hygiene: key material stays in the scratch root"
say "   producer closure: $("$NIX_STORE" --store "$PRODUCER" --query --requisites "$OUT_PATH" | tr '\n' ' ')"
mapfile -t PRODUCER_CLOSURE < <("$NIX_STORE" --store "$PRODUCER" --query --requisites "$OUT_PATH")
for closure_path in "${PRODUCER_CLOSURE[@]}"; do
  assert_no_secret_in "secret key is absent from closure member $(basename "$closure_path")" \
    "$PRODUCER$closure_path"
done
assert_no_secret_in "secret key is absent from the whole cache directory" "$CACHE"
assert_no_secret_in "secret key is absent from the substituted payload" "$READER1"
assert_contains "the log records the synthetic public key" "$PUBLIC_KEY" "$LOG"
assert_not_contains "the log records no secret key material" "$SECRET_KEY_BLOB" "$LOG"
assert_not_contains "the log records no secret key path" "$SECRET_KEY" "$LOG"
assert_equal "the signed cache holds no key-like file" "0" \
  "$(find "$CACHE" -name '*.secret' -o -name '*.key' -o -name 'secret*' | wc -l)"
assert_equal "the signed cache holds exactly one narinfo" "1" \
  "$(ls "$CACHE"/*.narinfo | wc -l)"
say ""

# -- summary ----------------------------------------------------------------
say "== summary"
say "   cases passed: $PASSED"
say "   cases failed: $FAILED"
if [ "$FAILED" -ne 0 ]; then
  say "RESULT: FAIL"
  exit 1
fi
say "RESULT: PASS"
exit 0
