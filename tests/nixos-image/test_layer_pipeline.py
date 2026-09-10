"""Portable controls for the exact build-time ancestor inventory adapter."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest

SOURCE = Path(os.environ.get("LAYER_PIPELINE_SCRIPT", Path(__file__).parents[2] / "lib/guest/layer-pipeline.py"))
SPEC = importlib.util.spec_from_file_location("guest_layer_pipeline", SOURCE)
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


def root(name):
    return "/nix/store/" + "0" * 32 + "-" + name


def config(layers):
    return {
        "architecture": "amd64", "config": {}, "os": "linux", "store_dir": "/nix/store",
        "from_image": None, "store_layers": layers, "customisation_layer": root("custom"),
        "repo_tag": "fixture:test", "created": "1970-01-01T00:00:01Z", "mtime": "1970-01-01T00:00:01Z",
        "uid": "0", "gid": "0", "uname": "root", "gname": "root",
    }


class LayerPipelineTests(unittest.TestCase):
    def test_exact_supplier_pipeline_and_immutable_inputs(self):
        inputs = [config([[root("lib"), root("base")]])]
        original = copy.deepcopy(inputs)
        self.assertEqual(pipeline.make_pipeline(inputs, 35), [
            ["remove_paths", [root("base"), root("lib")]],
            ["popularity_contest"], ["limit_layers", 35],
        ])
        self.assertEqual(inputs, original)

    def test_multiple_generations_include_every_ancestor_not_new_root(self):
        inputs = [config([[root("base"), root("lib")]]), config([[root("child")]])]
        removed = pipeline.make_pipeline(inputs, 2)[0][1]
        self.assertEqual(removed, [root("base"), root("child"), root("lib")])
        self.assertNotIn(root("grandchild"), removed)

    def test_union_is_deterministic_with_legacy_ancestor_overlap(self):
        a, b = config([[root("lib")], [root("base")]]), config([[root("lib"), root("child")]])
        self.assertEqual(pipeline.make_pipeline([a, b], 1), pipeline.make_pipeline([b, a], 1))

    def test_empty_layers_do_not_invent_paths_or_consume_new_budget(self):
        self.assertEqual(pipeline.make_pipeline([config([[], []])], 1), [
            ["remove_paths", []], ["popularity_contest"], ["limit_layers", 1],
        ])

    def test_nonpositive_and_wrong_type_layer_budgets(self):
        for value in (0, -1, True, 1.5, "2", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pipeline.make_pipeline([config([])], value)

    def test_missing_unknown_or_nonobject_ancestor_shape(self):
        missing, extra = config([]), config([])
        del missing["store_layers"]
        extra["extra_store_paths"] = []
        for value in (None, [], "metadata", missing, extra):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pipeline.make_pipeline([value], 1)

    def test_missing_or_excessive_ancestry(self):
        for value in ([], None, {}, [config([])] * 129):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pipeline.make_pipeline(value, 1)

    def test_wrong_store_directory_and_layer_types(self):
        for key, value in (("store_dir", "/other/store"), ("store_layers", None),
                           ("store_layers", [root("base")]), ("store_layers", [None])):
            candidate = config([])
            candidate[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                pipeline.make_pipeline([candidate], 1)

    def test_alias_traversal_subpath_and_non_store_paths(self):
        for path in (root("base") + "/bin", root("base") + "/", root("base") + "/../other",
                     root("base").replace("/nix/store/", "/nix//store/"), "/etc/passwd", "relative",
                     root("base") + "\n", root("base") + " ", root("base") + "é", None, True,
                     "/nix/store/" + "e" * 32 + "-bad-hash"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                pipeline.make_pipeline([config([[path]])], 1)

    def test_duplicate_path_within_or_between_layers_rejected(self):
        for layers in ([[root("base"), root("base")]], [[root("base")], [root("base")]]):
            with self.subTest(layers=layers), self.assertRaises(ValueError):
                pipeline.make_pipeline([config(layers)], 1)

    def test_complete_path_count_is_bounded(self):
        from unittest.mock import patch
        with patch.object(pipeline, "MAX_PATHS", 1), self.assertRaises(ValueError):
            pipeline.make_pipeline([config([[root("a")]]), config([[root("b")]])], 1)

    def test_json_duplicate_keys_and_nonfinite_constants_rejected(self):
        for data in (b'{"store_layers":[],"store_layers":[]}', b'{"config":{"a":1,"a":2}}', b'{"x":NaN}'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                pipeline.decode_config(data)

    def test_invalid_oversized_or_trailing_json_rejected(self):
        for data in (b"{", b"{}{}", b"\xff", b" " * (pipeline.MAX_CONFIG_BYTES + 1)):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                pipeline.decode_config(data)

    def test_valid_config_roundtrip(self):
        value = config([[root("lib")]])
        self.assertEqual(pipeline.decode_config(json.dumps(value).encode()), value)


if __name__ == "__main__":
    unittest.main()
