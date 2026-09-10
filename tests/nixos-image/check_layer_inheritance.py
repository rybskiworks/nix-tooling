"""Check real layered archives without extraction or running image contents."""

import hashlib
import json
import re
import sys
import tarfile


MAX_ENTRIES = 500_000
MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_METADATA_BYTES = 4 * 1024**2
STORE_PATH = re.compile(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9+._?=-]+")
LAYER_NAME = re.compile(r"[0-9a-f]{64}/layer\.tar")
CONFIG_NAME = re.compile(r"[0-9a-f]{64}\.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(data):
    require(len(data) <= MAX_METADATA_BYTES, "metadata exceeds bound")
    return json.loads(data, object_pairs_hook=unique_object,
                      parse_constant=lambda value: require(False, f"invalid JSON: {value}"))


def read_metadata(path):
    with open(path, "rb") as source:
        data = source.read(MAX_METADATA_BYTES + 1)
    require(len(data) <= MAX_METADATA_BYTES, "metadata exceeds bound")
    return data


def store_paths(value, label):
    require(type(value) is list, f"{label}: expected path list")
    require(all(type(path) is str and STORE_PATH.fullmatch(path) for path in value),
            f"{label}: invalid store path")
    require(len(value) == len(set(value)), f"{label}: duplicate store path")
    return set(value)


class HashedReader:
    def __init__(self, source, limit):
        self.source = source
        self.limit = limit
        self.count = 0
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        # tarfile's streaming reader uses bounded reads. Never expose read-all.
        require(0 <= size <= 1024**2, "unbounded stream read")
        data = self.source.read(size)
        self.count += len(data)
        require(self.count <= self.limit, "layer exceeds declared size")
        self.digest.update(data)
        return data


def normalized_path(name):
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    if name in ("", "."):
        return ""
    if name.startswith("/"):
        require(name == "/nix" or name.startswith("/nix/"), "unexpected absolute layer path")
        name = name[1:]
    parts = name.split("/")
    require(all(part not in ("", ".", "..") and "\x00" not in part for part in parts),
            "unsafe layer path")
    require(not any(part.startswith(".wh.") for part in parts), "unexpected whiteout")
    return name


def scan_layer(source, size, budget):
    reader = HashedReader(source, size)
    roots = set()
    referenced_roots = set()
    registrations = {}
    with tarfile.open(fileobj=reader, mode="r|") as archive:
        for member in archive:
            budget["entries"] += 1
            budget["inner_bytes"] += member.size
            require(budget["entries"] <= MAX_ENTRIES, "entry bound exceeded")
            require(budget["inner_bytes"] <= MAX_ARCHIVE_BYTES, "inner byte bound exceeded")
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(),
                    "unsupported layer member type")
            name = normalized_path(member.name)
            parts = name.split("/")
            if len(parts) >= 3 and parts[:2] == ["nix", "store"]:
                root = "/" + "/".join(parts[:3])
                require(STORE_PATH.fullmatch(root), "invalid archived store root")
                referenced_roots.add(root)
                if len(parts) == 3:
                    require(root not in roots, "duplicate store root header")
                    roots.add(root)
            if name.startswith("nix/guest-registrations/"):
                require(re.fullmatch(r"nix/guest-registrations/[a-z][a-z0-9-]*\.(registration|roots)", name),
                        "unexpected registration member")
                require(member.isfile() and member.size <= MAX_METADATA_BYTES,
                        "registration must be a bounded regular file")
                require(name not in registrations, "duplicate registration member")
                registrations[name] = archive.extractfile(member).read(MAX_METADATA_BYTES + 1)
                require(len(registrations[name]) == member.size, "truncated registration")
            archive.members.clear()
    while reader.read(1024**2):
        pass
    require(reader.count == size, "truncated layer bytes")
    require(referenced_roots == roots, "store descendants lack explicit root headers")
    return {"roots": roots, "registrations": registrations, "sha256": reader.digest.hexdigest()}


def scan_archive(path):
    layers = []
    metadata = {}
    seen = set()
    registrations = {}
    budget = {"entries": 0, "outer_bytes": 0, "inner_bytes": 0}
    with tarfile.open(path, mode="r|*") as archive:
        for member in archive:
            budget["entries"] += 1
            budget["outer_bytes"] += member.size
            require(budget["entries"] <= MAX_ENTRIES, "entry bound exceeded")
            require(budget["outer_bytes"] <= MAX_ARCHIVE_BYTES, "outer byte bound exceeded")
            require(member.isfile() and member.name not in seen, "duplicate or nonregular outer member")
            seen.add(member.name)
            source = archive.extractfile(member)
            if LAYER_NAME.fullmatch(member.name):
                layer = scan_layer(source, member.size, budget)
                require(layer["sha256"] == member.name.split("/")[0], "layer digest mismatch")
                layer.update(name=member.name, size=member.size)
                require(not registrations.keys() & layer["registrations"].keys(),
                        "registration overwritten by another layer")
                registrations.update(layer["registrations"])
                layers.append(layer)
            else:
                require(member.name == "manifest.json" or CONFIG_NAME.fullmatch(member.name),
                        "unexpected outer member")
                require(member.size <= MAX_METADATA_BYTES, "metadata exceeds bound")
                metadata[member.name] = source.read(MAX_METADATA_BYTES + 1)
            archive.members.clear()
    require("manifest.json" in metadata, "manifest missing")
    manifest = decode_json(metadata["manifest.json"])
    require(type(manifest) is list and len(manifest) == 1 and type(manifest[0]) is dict,
            "expected exactly one image manifest")
    manifest = manifest[0]
    require(set(manifest) == {"Config", "RepoTags", "Layers"}, "unexpected manifest shape")
    require(type(manifest["Config"]) is str and CONFIG_NAME.fullmatch(manifest["Config"]),
            "invalid image configuration name")
    require(set(metadata) == {"manifest.json", manifest["Config"]}, "image metadata missing or extra")
    config_bytes = metadata[manifest["Config"]]
    require(hashlib.sha256(config_bytes).hexdigest() + ".json" == manifest["Config"],
            "image configuration digest mismatch")
    image_config = decode_json(config_bytes)
    require(manifest["Layers"] == [layer["name"] for layer in layers] and layers,
            "manifest layer order or inventory mismatch")
    require(image_config.get("rootfs") == {
        "type": "layers", "diff_ids": ["sha256:" + layer["sha256"] for layer in layers]
    }, "image diff IDs mismatch")
    require(type(image_config.get("history")) is list and len(image_config["history"]) == len(layers),
            "layer history count mismatch")
    return {"layers": layers, "registrations": registrations, "budget": budget,
            "manifest": manifest}


def verify_image(spec, actual, parent, ancestor_roots):
    conf = decode_json(read_metadata(spec["streamConfig"]))
    require(type(conf) is dict and conf.get("store_dir") == "/nix/store", "invalid stream configuration")
    require(conf.get("from_image") == (parent["archive"] if parent else None),
            "stream parent identity mismatch")
    require(type(conf.get("store_layers")) is list, "missing supplier store layer inventory")
    expected_layers = [store_paths(layer, "supplier layer") for layer in conf["store_layers"]]
    own_roots = set().union(*expected_layers)
    require(sum(map(len, expected_layers)) == len(own_roots), "duplicate supplier store paths")
    require(not own_roots & ancestor_roots, "ancestor store path emitted again")
    inherited = parent["actual"]["layers"] if parent else []
    layers = actual["layers"]
    require(len(layers) == len(inherited) + len(expected_layers) + 1, "added layer count mismatch")
    require(len(layers) <= spec["maxLayers"], "image layer budget exceeded")
    for before, after in zip(inherited, layers):
        require(all(before[key] == after[key] for key in ("name", "size", "sha256")),
                "inherited layer prefix bytes changed")
    added = layers[len(inherited):]
    require([layer["roots"] for layer in added[:-1]] == expected_layers,
            "actual store root headers differ from supplier inventory")
    require(not added[-1]["roots"], "customization layer contains unaccounted store roots")
    roots = store_paths(spec["roots"], "registration roots")
    require(len(roots) == 2 and roots <= own_roots, "new payload/config roots missing")
    closure_bytes = read_metadata(spec["fullStorePaths"])
    full_closure = store_paths(closure_bytes.decode().splitlines(), "independent full closure")
    require(roots <= full_closure, "independent closure omits roots")
    require(full_closure <= ancestor_roots | own_roots, "full registered closure missing from archive")
    reused = store_paths(spec["reusedRoots"], "deliberately reused roots")
    require(reused <= ancestor_roots and reused <= full_closure, "deliberate ancestor overlap absent")
    prefix = "nix/guest-registrations/" + spec["registrationName"]
    expected = {
        prefix + ".registration": read_metadata(spec["fullRegistration"]),
        prefix + ".roots": ("\n".join(spec["roots"]) + "\n").encode(),
    }
    require(added[-1]["registrations"] == expected,
            "registration or roots differ from independent full closure")
    require(all(not layer["registrations"] for layer in added[:-1]),
            "registration unexpectedly embedded in store layer")
    all_expected = (parent["actual"]["registrations"] if parent else {}) | expected
    require(actual["registrations"] == all_expected, "registration history changed")
    require(actual["manifest"]["RepoTags"] == [conf["repo_tag"]], "archive tag mismatch")
    return own_roots, {
        "archive": spec["archive"], "layers": len(layers), "inheritedLayers": len(inherited),
        "ownStorePaths": len(own_roots), "inheritedStorePaths": len(ancestor_roots),
        "fullRegistrationPaths": len(full_closure), "reusedRoots": sorted(reused),
        "registrationSHA256": hashlib.sha256(expected[prefix + ".registration"]).hexdigest(),
        "layersSHA256": [layer["sha256"] for layer in layers], "boundsObserved": actual["budget"],
    }


def check_chain(specs):
    require(type(specs) is list and len(specs) == 3, "expected parent, child and grandchild")
    parent = None
    ancestor_roots = set()
    names = set()
    reports = []
    fields = {"archive", "streamConfig", "registrationName", "roots", "fullRegistration",
              "fullStorePaths", "reusedRoots", "maxLayers"}
    for spec in specs:
        require(type(spec) is dict and set(spec) == fields, "unexpected image specification fields")
        name = spec["registrationName"]
        require(type(name) is str and re.fullmatch(r"[a-z][a-z0-9-]*", name) and name not in names,
                "invalid or duplicate registration name")
        require(type(spec["maxLayers"]) is int and 1 <= spec["maxLayers"] <= 125,
                "invalid layer budget")
        require(bool(spec["reusedRoots"]) == bool(parent), "each descendant must deliberately overlap")
        names.add(name)
        actual = scan_archive(spec["archive"])
        own_roots, report = verify_image(spec, actual, parent, ancestor_roots)
        ancestor_roots |= own_roots
        parent = {"archive": spec["archive"], "actual": actual}
        reports.append(report)
    return {"status": "passed", "images": reports,
            "proof": "archive inheritance and complete registration; no image execution"}


if __name__ == "__main__":
    require(len(sys.argv) == 2, "usage: check_layer_inheritance.py SPEC.json")
    print(json.dumps(check_chain(decode_json(read_metadata(sys.argv[1]))), indent=2, sort_keys=True))
