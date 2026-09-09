# Image metadata is immutable; the database and GC roots belong to this guest.
# Do not use a socket, a host database or a per-layer replacement database.
register_images() (
set -euo pipefail
local metadata=$1 state=$2 store=$3
export NIX_REMOTE=local NIX_STATE_DIR="$state" NIX_STORE_DIR="$store"
export NIX_USER_CONF_FILES=/dev/null
local gcroots="$state/gcroots/guest-images"
test -d "$metadata" && test ! -L "$metadata"
mkdir -p "$state" "$gcroots"
exec 9>"$state/guest-registration.lock"
flock -x 9
shopt -s nullglob
manifests=("$metadata"/*.roots)
test "${#manifests[@]}" -gt 0
test -f "$metadata/base.roots"
for registration in "$metadata"/*.registration; do
  test -f "${registration%.registration}.roots"
done
nix-store --init
for manifest in "${manifests[@]}"; do
  name=${manifest##*/}
  name=${name%.roots}
  [[ "$name" =~ ^[a-z][a-z0-9-]*$ ]]
  registration="$metadata/$name.registration"
  test -f "$manifest" && test ! -L "$manifest"
  test -f "$registration" && test ! -L "$registration"
  mapfile -t roots < "$manifest"
  test "${#roots[@]}" -gt 0
  for root in "${roots[@]}"; do
    [[ "$root" == "$store/"* ]]
    [[ "${root#"$store/"}" =~ ^[0-9a-df-np-sv-z]{32}-[a-zA-Z0-9+._?=-]+$ ]]
    test -e "$root"
  done
  nix-store --load-db < "$registration"
  nix-store --check-validity "${roots[@]}"
  closure=$(nix-store --query --requisites "${roots[@]}")
  test -n "$closure"
  while IFS= read -r member; do
    [[ "$member" == "$store/"* ]]
    [[ "${member#"$store/"}" =~ ^[0-9a-df-np-sv-z]{32}-[a-zA-Z0-9+._?=-]+$ ]]
    test -e "$member"
  done <<< "$closure"
  mkdir -p "$gcroots/$name"
  for index in "${!roots[@]}"; do
    ln -sfn "${roots[$index]}" "$gcroots/$name/$index"
  done
done
)

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  register_images /nix/guest-registrations /nix/var/nix /nix/store
fi
