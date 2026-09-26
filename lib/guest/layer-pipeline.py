"""Build a dockerTools leaf pipeline from immutable ancestor stream configs."""

import json
import argparse
import re
import sys

MAX_CONFIG_BYTES = 4 * 1024 * 1024
MAX_PATHS = 100_000
FIELDS = {
    "architecture", "config", "os", "store_dir", "from_image", "store_layers",
    "customisation_layer", "repo_tag", "created", "mtime", "uid", "gid", "uname", "gname",
}
STORE_PATH = re.compile(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9+._?=-]+")


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate ancestor configuration key")
        value[key] = item
    return value


def decode_config(data):
    if not isinstance(data, bytes) or len(data) > MAX_CONFIG_BYTES:
        raise ValueError("ancestor configuration exceeds the byte budget")
    return json.loads(data, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def decode_inventory(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_CONFIG_BYTES:
        raise ValueError("external inventory is empty or exceeds the byte budget")
    if not data.endswith(b"\n") or b"\r" in data:
        raise ValueError("external inventory requires canonical newline records")
    paths = data.decode("utf-8").splitlines()
    if not paths or len(paths) > MAX_PATHS or len(set(paths)) != len(paths):
        raise ValueError("external inventory repeats paths or exceeds the path budget")
    if any(STORE_PATH.fullmatch(path) is None for path in paths):
        raise ValueError("external inventory contains a noncanonical store root")
    return paths


def make_pipeline(configs, max_layers, external_inventories=None):
    if type(max_layers) is not int or max_layers < 1:
        raise ValueError("a positive store-layer budget is required")
    external_inventories = [] if external_inventories is None else external_inventories
    if not isinstance(configs, list) or len(configs) > 128:
        raise ValueError("a bounded ancestor configuration list is required")
    if not isinstance(external_inventories, list) or len(external_inventories) > 128:
        raise ValueError("a bounded external inventory list is required")
    if not configs and not external_inventories:
        raise ValueError("an ancestor or external inventory is required")
    inherited = set()
    count = 0
    for config in configs:
        if (not isinstance(config, dict) or set(config) != FIELDS
                or config["store_dir"] != "/nix/store"
                or not isinstance(config["store_layers"], list)):
            raise ValueError("unsupported ancestor stream configuration")
        seen = set()
        for layer in config["store_layers"]:
            if not isinstance(layer, list):
                raise ValueError("ancestor store layer must be a path list")
            for path in layer:
                count += 1
                if count > MAX_PATHS:
                    raise ValueError("ancestor path inventory exceeds the budget")
                if not isinstance(path, str) or STORE_PATH.fullmatch(path) is None:
                    raise ValueError("ancestor store path is not a canonical store root")
                if path in seen:
                    raise ValueError("ancestor repeats a store path")
                seen.add(path)
        inherited.update(seen)
    for paths in external_inventories:
        if not isinstance(paths, list) or not paths:
            raise ValueError("external inventory must be a nonempty path list")
        seen = set()
        for path in paths:
            count += 1
            if count > MAX_PATHS:
                raise ValueError("combined path inventory exceeds the budget")
            if not isinstance(path, str) or STORE_PATH.fullmatch(path) is None:
                raise ValueError("external store path is not a canonical store root")
            if path in seen:
                raise ValueError("external inventory repeats a store path")
            seen.add(path)
        inherited.update(seen)
    return [["remove_paths", sorted(inherited)], ["popularity_contest"], ["limit_layers", max_layers]]


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("max_layers", type=int)
    parser.add_argument("ancestors", nargs="*")
    parser.add_argument("--external", action="append", default=[])
    args = parser.parse_args(argv)
    if len(args.ancestors) > 128 or len(args.external) > 128:
        raise ValueError("too many closure inventories")
    configs = []
    for path in args.ancestors:
        with open(path, "rb") as source:
            configs.append(decode_config(source.read(MAX_CONFIG_BYTES + 1)))
    inventories = []
    for path in args.external:
        with open(path, "rb") as source:
            inventories.append(decode_inventory(source.read(MAX_CONFIG_BYTES + 1)))
    # Validate the complete inventory before producing any pipeline bytes.
    print(json.dumps(make_pipeline(configs, args.max_layers, inventories), separators=(",", ":")))


if __name__ == "__main__":
    main(sys.argv[1:])
