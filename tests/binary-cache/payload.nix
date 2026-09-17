# The single trivial derivation the signed-cache oracle publishes.
#
# It deliberately depends on nothing: no nixpkgs input, no network, no shared
# store. The builder is the host shell and it writes one fixed marker script
# into $out, so every store the oracle creates can be a private chroot store
# and the whole flow stays offline.
derivation {
  name = "signed-cache-oracle-payload";
  system = builtins.currentSystem;
  builder = "/bin/sh";
  args = [
    "-c"
    ''
      printf '%s\n' '#!/bin/sh' 'printf "%s\n" signed-cache-oracle-payload-v1' > "$out"
    ''
  ];
}
