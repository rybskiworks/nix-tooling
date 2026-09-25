# Shared SOPS integration

The immutable `sops-nix` input selects the upstream NixOS and Home Manager
modules. Its nixpkgs follows the shared tooling package set. Public interfaces:

- `nixosModules.sops`: the upstream NixOS module.
- `homeManagerModules.sops`: the upstream Home Manager module.
- `packages.x86_64-linux.sops` and `age`: the shared command-line packages.
- `packages.x86_64-linux.sops-install-secrets`: the exact upstream installer used
  by the NixOS module with the shared package set.

Import the appropriate module once. Consumers own encrypted files, public
recipient policy, secret names, runtime destinations, ownership and service
ordering. The module export alone declares no secrets, generates no identity and
performs no decryption. NixOS's `sops.package` option selects the installer, not
the standalone `sops` command.

Keep private identities and plaintext out of Nix expressions, derivations, images
and the Nix store. Enroll decryption identities independently; activation must
not depend on decrypting the identity that activation itself needs. A new host
must be admitted to encrypted recipient sets before it can decrypt existing
material. Secret enrollment and rotation remain deliberate operator actions.

Host sops-nix delivery and Workestrate's existing guest secret custody are
separate integrations. Importing this module does not change guest injection,
copy host credentials, or make a runtime secret path available in another VM.
Sharing a source pin does not merge those authority boundaries.

The ordinary `lix-system-contract` evaluates NixOS composition and verifies the
installer identity and absence of implicit secrets/key generation. It does not
prove decryption, Home Manager activation, credential enrollment or a live
service restart; those require synthetic-secret tests in the consuming system.
