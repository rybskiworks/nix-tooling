"""Portable negative controls for the opt-in real-archive checker."""

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


module_spec = importlib.util.spec_from_file_location(
    "layer_inheritance",
    os.environ.get("LAYER_INHERITANCE_SCRIPT", str(Path(__file__).with_name("check_layer_inheritance.py"))),
)
check = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(check)


def store(number, name):
    return "/nix/store/" + str(number) * 32 + "-" + name


def tar_bytes(members):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as archive:
        for name, contents in members:
            member = tarfile.TarInfo(name)
            if contents is None:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            else:
                member.size = len(contents)
                archive.addfile(member, io.BytesIO(contents))
    return result.getvalue()


def store_layer(paths, content=b"payload"):
    members = [("/nix", None), ("/nix/store", None)]
    for path in paths:
        members.extend([(path, None), (path + "/file", content)])
    return tar_bytes(members)


def image_bytes(layers, tag, *, names=None, manifest_mutation=None):
    names = names or [hashlib.sha256(layer).hexdigest() + "/layer.tar" for layer in layers]
    config = json.dumps({
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()
                                                     for layer in layers]},
        "history": [{} for _ in layers],
    }).encode()
    config_name = hashlib.sha256(config).hexdigest() + ".json"
    manifest = [{"Config": config_name, "RepoTags": [tag], "Layers": names[:]}]
    if manifest_mutation:
        manifest_mutation(manifest)
    return tar_bytes(list(zip(names, layers)) + [(config_name, config),
                                                ("manifest.json", json.dumps(manifest).encode())])


class InheritanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.directory_path = Path(self.directory.name)
        self.glibc = store(1, "glibc")
        self.hello = store(2, "hello")
        self.specs = []
        self.layer_sets = []
        self.configs = []
        self.own_paths = []
        self.registrations = []
        for index, name in enumerate(("base", "example", "grandchild")):
            roots = [store(index + 3, name + "-payload"), store(index + 6, name + "-config")]
            own = roots + ([self.glibc] if index == 0 else [self.hello] if index == 1 else [])
            reused = [] if index == 0 else [self.glibc] if index == 1 else [self.glibc, self.hello]
            registration = ("full-registration-" + name + "\n" + "\n".join(own + reused)).encode()
            spec = {
                "archive": str(self.directory_path / (name + ".tar")),
                "streamConfig": str(self.directory_path / (name + ".json")),
                "registrationName": name, "roots": roots,
                "fullRegistration": str(self.directory_path / (name + ".registration")),
                "fullStorePaths": str(self.directory_path / (name + ".paths")),
                "reusedRoots": reused, "maxLayers": 20,
            }
            config = {
                "store_dir": "/nix/store", "store_layers": [own], "repo_tag": name + ":test",
                "from_image": self.specs[-1]["archive"] if self.specs else None,
            }
            customization = tar_bytes([
                (".", None), ("nix/guest-registrations", None),
                ("nix/guest-registrations/" + name + ".registration", registration),
                ("nix/guest-registrations/" + name + ".roots", ("\n".join(roots) + "\n").encode()),
            ])
            self.layer_sets.append((self.layer_sets[-1][:] if index else []) + [store_layer(own), customization])
            self.configs.append(config)
            self.own_paths.append(own)
            self.registrations.append(registration)
            self.specs.append(spec)
            Path(spec["fullRegistration"]).write_bytes(registration)
            Path(spec["fullStorePaths"]).write_text("\n".join(own + reused) + "\n")
            self.write_image(index)

    def write_image(self, index, **kwargs):
        Path(self.specs[index]["archive"]).write_bytes(
            image_bytes(self.layer_sets[index], self.configs[index]["repo_tag"], **kwargs))
        Path(self.specs[index]["streamConfig"]).write_text(json.dumps(self.configs[index]))

    def reject(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            check.check_chain(self.specs)

    def test_three_generations_with_actual_overlap(self):
        result = check.check_chain(self.specs)
        self.assertEqual(result["status"], "passed")
        self.assertEqual([item["inheritedLayers"] for item in result["images"]], [0, 2, 4])
        self.assertEqual(result["images"][2]["reusedRoots"], [self.glibc, self.hello])

    def test_duplicate_outer_layer(self):
        self.layer_sets[1].insert(2, self.layer_sets[1][0])
        self.write_image(1)
        self.reject("duplicate or nonregular outer member")

    def test_changed_inherited_bytes_with_valid_new_digest(self):
        self.layer_sets[1][0] = store_layer(self.own_paths[0], b"changed")
        self.write_image(1)
        self.reject("inherited layer prefix bytes changed")

    def test_reordered_prefix(self):
        self.layer_sets[1][:2] = reversed(self.layer_sets[1][:2])
        self.write_image(1)
        self.reject("inherited layer prefix bytes changed")

    def test_repeated_ancestor_path_in_different_layer(self):
        self.configs[1]["store_layers"][0].append(self.glibc)
        self.layer_sets[1][-2] = store_layer(self.own_paths[1])
        self.write_image(1)
        self.reject("ancestor store path emitted again")

    def test_hidden_ancestor_path_missing_from_config(self):
        self.layer_sets[1][-2] = store_layer(self.own_paths[1] + [self.glibc])
        self.write_image(1)
        self.reject("actual store root headers differ")

    def test_missing_new_config_root(self):
        self.configs[1]["store_layers"][0].remove(self.specs[1]["roots"][1])
        self.layer_sets[1][-2] = store_layer(self.own_paths[1])
        self.write_image(1)
        self.reject("new payload/config roots missing")

    def test_missing_transitive_closure_path(self):
        with open(self.specs[1]["fullStorePaths"], "a") as target:
            target.write(store(9, "missing") + "\n")
        self.reject("full registered closure missing")

    def test_pruned_registration_rejected(self):
        Path(self.specs[1]["fullRegistration"]).write_bytes(b"different complete closure")
        self.reject("registration or roots differ")

    def test_missing_root_line_rejected(self):
        self.specs[1]["roots"].reverse()
        self.reject("registration or roots differ")

    def test_customization_cannot_hide_store_payload(self):
        self.layer_sets[1][-1] = store_layer([store(9, "hidden")])
        self.write_image(1)
        self.reject("customization layer contains unaccounted store roots")

    def test_registration_cannot_be_overwritten(self):
        self.layer_sets[1][-1] = self.layer_sets[0][-1]
        self.write_image(1)
        self.reject("duplicate or nonregular outer member")

    def test_registration_overwrite_with_distinct_layer_digest(self):
        self.layer_sets[1][-1] = tar_bytes([
            ("nix/guest-registrations/base.registration", b"changed"),
        ])
        self.write_image(1)
        self.reject("registration overwritten by another layer")

    def test_empty_declared_layer_still_has_an_actual_layer(self):
        # Empty path groups are valid input inventories, but are not permission
        # to omit the corresponding layer bytes from the archive under test.
        self.configs[0]["store_layers"].insert(0, [])
        self.write_image(0)
        self.reject("added layer count mismatch")

    def test_invalid_supplier_path_is_rejected(self):
        self.configs[1]["store_layers"][0].append("/nix/store/../escape")
        self.write_image(1)
        self.reject("supplier layer: invalid store path")

    def test_deliberate_overlap_is_required(self):
        self.specs[1]["reusedRoots"] = [store(9, "not-inherited")]
        self.reject("deliberate ancestor overlap absent")

    def test_stream_parent_identity(self):
        self.configs[1]["from_image"] = self.specs[2]["archive"]
        self.write_image(1)
        self.reject("stream parent identity mismatch")

    def test_layer_budget(self):
        self.specs[1]["maxLayers"] = 3
        self.reject("image layer budget exceeded")

    def test_missing_manifest_layer(self):
        self.write_image(1, manifest_mutation=lambda manifest: manifest[0]["Layers"].pop())
        self.reject("manifest layer order or inventory mismatch")

    def test_wrong_layer_digest(self):
        names = [hashlib.sha256(layer).hexdigest() + "/layer.tar" for layer in self.layer_sets[1]]
        names[-1] = "0" * 64 + "/layer.tar"
        self.write_image(1, names=names)
        self.reject("layer digest mismatch")

    def test_unsafe_path_rejected_before_extraction(self):
        self.layer_sets[1][-2] = tar_bytes([("../escape", b"never extracted")])
        self.write_image(1)
        self.reject("unsafe layer path")
        self.assertFalse((self.directory_path.parent / "escape").exists())

    def test_missing_store_root_header(self):
        self.layer_sets[1][-2] = tar_bytes([(self.hello + "/file", b"no root header")])
        self.write_image(1)
        self.reject("store descendants lack explicit root headers")

    def test_byte_and_entry_bounds(self):
        for bound, value, message in (("MAX_ENTRIES", 1, "entry bound exceeded"),
                                      ("MAX_ARCHIVE_BYTES", 1, "outer byte bound exceeded")):
            with self.subTest(bound=bound), patch.object(check, bound, value):
                self.reject(message)

    def test_json_and_spec_negatives(self):
        for data in (b'{"store_layers":[],"store_layers":[]}', b'{"value":NaN}'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                check.decode_json(data)
        invalid = copy.deepcopy(self.specs)
        invalid[0]["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unexpected image specification"):
            check.check_chain(invalid)


if __name__ == "__main__":
    unittest.main()
