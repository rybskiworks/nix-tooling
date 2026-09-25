"""Inspect real tiny archives for absent external payload and complete registration."""

import io
import json
from pathlib import Path
import sys
import tarfile


def inspect_archive(path, admitted):
    found = set()
    metadata = {}
    with tarfile.open(path, "r:*") as archive:
        docker = json.load(archive.extractfile("manifest.json"))
        if len(docker) != 1:
            raise ValueError("expected exactly one fixture image")
        for layer_path in docker[0]["Layers"]:
            with archive.extractfile(layer_path) as layer_file:
                with tarfile.open(fileobj=io.BytesIO(layer_file.read()), mode="r:") as layer:
                    for member in layer:
                        name = member.name.removeprefix("./").lstrip("/")
                        for root in admitted:
                            relative = root.removeprefix("/")
                            if name == relative or name.startswith(relative + "/"):
                                found.add(root)
                        if name.startswith(("nix/guest-registrations/", "nix/guest-external-closures/")) and member.isfile():
                            metadata[name] = layer.extractfile(member).read()
    return found, metadata


def check(spec, retained_graph):
    export = Path(spec["export"])
    manifest = json.loads((export / "manifest.json").read_text())
    admitted = (export / "store-paths").read_text().splitlines()
    if [entry["path"] for entry in manifest["entries"]] != admitted:
        raise ValueError("manifest and inventory disagree")
    if not set(admitted).issubset(retained_graph.read_text().splitlines()):
        raise ValueError("the export does not retain every admitted member through Nix references")
    if {entry["kind"] for entry in manifest["entries"]} != {"directory", "regular"}:
        raise ValueError("fixture must exercise directory and regular-file store roots")
    found, metadata = inspect_archive(spec["archive"], admitted)
    if found:
        raise ValueError("external closure bytes were embedded in an image layer")
    control_found, _ = inspect_archive(spec["control"], admitted)
    if set(admitted) != control_found:
        raise ValueError("unfiltered control did not embed the complete fixture closure")
    registration = metadata["nix/guest-registrations/example.registration"]
    if registration != Path(spec["registration"]).read_bytes():
        raise ValueError("external filtering pruned guest registration")
    if any(path.encode() not in registration.splitlines() for path in admitted):
        raise ValueError("registration omitted an admitted member")
    if metadata["nix/guest-external-closures/1.json"] != (export / "manifest.json").read_bytes():
        raise ValueError("image external manifest differs from the admitted export")
    # The export is metadata, not another payload archive or copied store tree.
    if sorted(path.name for path in export.iterdir()) != ["manifest.json", "registration", "roots", "store-paths"]:
        raise ValueError("closure export contains unexpected payload files")
    return {"admitted_paths": len(admitted), "external_payload_paths_embedded": 0,
            "control_embedded_paths": len(control_found), "full_registration_retained": True,
            "export_retains_complete_closure": True}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise ValueError("expected a fixture specification and exported Nix reference graph")
    print(json.dumps(check(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2])), sort_keys=True))
