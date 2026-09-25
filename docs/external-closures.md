# External immutable closures

An external closure lets several guests use the host's exact immutable store
objects without embedding those objects in every image. Package identity alone
does not establish physical sharing: the runtime must bind the admitted objects
read-only before init, with no staging copy or writable backing.

The supplier exports metadata and filters image layers. It does not configure a
runtime, start a guest, share the host daemon, or mount the host's Nix database.
Every guest keeps its own writable database and store for additional builds.

## Build interface

```nix
let
  export = tooling.lib.guest.mkClosureExport {
    inherit pkgs;
    name = "application";
    roots = [ application ];
  };
in tooling.lib.guest.mkNixosImage {
  inherit pkgs;
  name = "application-base";
  stateVersion = "26.05";
  externalClosures = [ export ];
  modules = [
    tooling.nixosModules.lixGuest
    { environment.systemPackages = [ application ]; }
  ];
}
```

Select the application derivation once and use that exact output on the host and
in the guest. The example's stateVersion is an explicit compatibility choice;
existing consumers must retain their own value. This library has no dependency
on any particular application repository.

`mkClosureExport` accepts a lowercase identifier `name`, an explicit `pkgs`, and
1–64 unique derivation or Nix path roots. Nix computes their complete closure.
The export has no payload copy: its references retain the immutable admitted
objects. Hold a normal host GC root on the export for as long as any running or
restartable guest needs those objects. A manifest on disk without a GC root is
not a retention guarantee.

`mkNixosImage.externalClosures` accepts these exports and omits their admitted
store paths from its layers. `mkNixosLayer` inherits the base's complete external
set and also filters new leaf layers. Choose the set when constructing the
earliest base: adding an exclusion to a leaf cannot remove bytes already present
in a parent archive. Full guest closure registration and boot references remain
intact; omission of a mount must therefore fail startup rather than substitute a
different package.

Images expose `guestExternalClosures` in passthru and copy each export manifest
to `/nix/guest-external-closures/1.json`, `2.json`, and so on. The image does not
configure those mounts automatically. `guestRegistration` continues to describe
the complete registered closure, including external objects.

## Export format and runtime admission

Each export contains exactly four read-only metadata files:

- `manifest.json`: schema version, explicit roots, sorted admitted paths and kinds.
- `store-paths`: the same sorted paths, one canonical `/nix/store/...` root per line.
- `roots`: the explicit roots, one per line.
- `registration`: the full Nix closure registration, unchanged.

The schema is:

```json
{
  "schema_version": 1,
  "store_dir": "/nix/store",
  "roots": ["/nix/store/<hash>-application"],
  "entries": [
    {"path": "/nix/store/<hash>-application", "kind": "directory"}
  ],
  "store_paths_file": "store-paths",
  "registration_file": "registration"
}
```

`guestClosureExport` passthru exposes `schemaVersion`, `roots` and the supporting
`closureInfo` derivation as `closure`. Consumers can retain the export's exact
output/derivation identity and manifest path rather than duplicate the inventory.

Admission rejects duplicate or malformed roots, oversized inventories, special
files and top-level symlink outputs. A root's kind is measured with `lstat`;
symlinks are never silently resolved. Symlinks within an admitted directory are
ordinary Nix content. Regular-file outputs are represented as `kind: "regular"`,
but a runtime must prove that its file-mount implementation shares their backing
rather than copying them. A runtime unable to do so must reject that closure.

Before init, a runtime must validate the selected immutable export, retain it,
and bind every admitted path at the identical guest store path read-only. It must
reject unexpected paths, missing members, writable bindings, host database or
daemon mounts, and copied staging files. It must also prevent host-side mutation
of the backing store through any admitted writable alias. Host administration
remains trusted; read-only guest mounts do not confine a malicious host.

## Verification boundaries

The ordinary `external-closure-image` check builds tiny synthetic archives. It
tests directory and regular-file outputs, the export's Nix reference retention,
absence from every image layer, complete registration, inherited exclusions and
an unfiltered positive control. Portable tests cover malformed and unsupported
inputs before output creation. The fixture is not a bootable guest.

Actual physical sharing needs a separate native test: inspect the active exact
read-only bindings, compare host/guest executable identity, reject staging copies,
and verify each guest has an independent writable database. Test missing-mount
failure, retained closure lifetime, fresh guest builds and restart behavior with
the selected runtime. Passing archive checks alone does not qualify those runtime
properties or a workstation deployment.
