"""Build a dockerTools leaf pipeline from immutable ancestor stream configs."""

import json
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


def make_pipeline(configs, max_layers):
    if type(max_layers) is not int or max_layers < 1:
        raise ValueError("a positive store-layer budget is required")
    if not isinstance(configs, list) or not 1 <= len(configs) <= 128:
        raise ValueError("a bounded nonempty ancestor configuration list is required")
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
    return [["remove_paths", sorted(inherited)], ["popularity_contest"], ["limit_layers", max_layers]]


def main(argv):
    if not 2 <= len(argv) <= 129:
        raise ValueError("expected a layer budget and ancestor stream configs")
    configs = []
    for path in argv[1:]:
        with open(path, "rb") as source:
            configs.append(decode_config(source.read(MAX_CONFIG_BYTES + 1)))
    # Validate the complete inventory before producing any pipeline bytes.
    print(json.dumps(make_pipeline(configs, int(argv[0])), separators=(",", ":")))


if __name__ == "__main__":
    main(sys.argv[1:])
