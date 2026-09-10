# NixOS image checks

The ordinary `nixos-image-contract` check runs portable registration, archive
checker and runtime-probe contract tests. It does not realize an image or run a
virtual machine.

The separate archive inheritance check is explicitly opt-in:

```sh
nix build .#legacyPackages.x86_64-linux.nixosImages.layerInheritance \
  --no-update-lock-file --option allow-import-from-derivation false
```

This realizes the common parent, a registered example child, and its registered
grandchild. Both descendants deliberately reuse ancestor store paths. The check
streams each actual archive without extraction, verifies unique outer layers,
hashes the complete inherited prefix, and compares newly emitted store root
headers with the supplier's generated layer inventory. It rejects ancestor
paths emitted again, missing new payload/config roots, and registration data
that differs from an independently computed complete closure. All ancestor
registrations must remain intact.

The resulting `result.json` records layer hashes, closure counts and observed
scan bounds. Each image is limited to 500,000 archive entries and 8 GiB of declared
payload; individual metadata files are limited to 4 MiB. Image assembly and the
three archive scans require additional disk and CPU time and should be admitted
separately from the small ordinary contract. Passing this check establishes
archive construction, not guest activation, daemon operation or shutdown.
