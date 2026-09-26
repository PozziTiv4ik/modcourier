import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tests.helpers import config, jar
from modcourier.config import Config, local_path
from modcourier.errors import CourierError
from modcourier.inspect import artifacts, suggested_config
from modcourier.metadata import exact_versions, read_artifact


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_four_loaders_and_checksums(self):
        for loader in ("fabric", "forge", "neoforge", "quilt"):
            with self.subTest(loader=loader):
                item = read_artifact(jar(self.root, loader))
                self.assertEqual(item.loaders, [loader])
                self.assertEqual(item.game_versions, ["1.21.1"])
                self.assertEqual(item.version, "1.0.0")
                self.assertEqual(len(item.hashes["sha512"]), 128)

    def test_ranges_are_not_expanded_to_untested_versions(self):
        for value in (">=1.20", "[1.20,1.22)", "*", "~1.21", ["1.20.1", ">=1.21"]):
            self.assertEqual(exact_versions(value), [])
        self.assertEqual(exact_versions("[1.21.1]"), ["1.21.1"])
        self.assertEqual(exact_versions("=1.21.1"), ["1.21.1"])
        self.assertEqual(exact_versions("24w14a"), ["24w14a"])

    def test_forge_manifest_version(self):
        path = jar(self.root, "forge")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("META-INF/mods.toml", '[[mods]]\nmodId="demo"\nversion="' + "$" + '{file.jarVersion}"\n')
            archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\nImplementation-Version: 2.0.\r\n 1\r\n")
        self.assertEqual(read_artifact(path).version, "2.0.1")

    def test_unresolved_version_rejected(self):
        path = jar(self.root, version="$" + "{version}")
        with self.assertRaisesRegex(CourierError, "build the release"):
            read_artifact(path)

    def test_dependencies_exclude_runtime(self):
        path = jar(self.root, deps={"fabricloader": ">=0.16", "fabric-api": "*"})
        deps = read_artifact(path).dependencies
        self.assertEqual([(d.mod_id, d.kind) for d in deps], [("fabric-api", "required")])

    def test_old_builds_are_not_published_together(self):
        jar(self.root, version="1.0.0")
        jar(self.root, version="2.0.0")
        with self.assertRaisesRegex(CourierError, "Old and new"):
            artifacts(config(self.root))

    def test_source_jars_are_excluded(self):
        jar(self.root)
        jar(self.root, name="demo-sources.jar")
        self.assertEqual(len(artifacts(config(self.root))), 1)

    def test_configuration_cannot_read_outside_project(self):
        with self.assertRaises(CourierError):
            local_path(self.root, "../secret.txt")

    def test_invalid_zip(self):
        path = self.root / "bad.jar"
        path.write_text("not a zip")
        with self.assertRaisesRegex(CourierError, "Cannot read"):
            read_artifact(path)

    def test_config_rejects_tokens_and_typos(self):
        for raw in (
            {"platforms": {"modrinth": {"token": "never store this"}}},
            {"project": {"token": "never store this"}},
            {"release": {"game_versions": "1.21.1"}},
            {"platforms": {"modrinth": {"enabled": "false"}}},
            {"artifact": "typo"},
        ):
            with self.subTest(raw=raw), self.assertRaises(CourierError):
                Config(self.root, raw)

    def test_init_preserves_variant_specific_versions(self):
        first = read_artifact(jar(self.root, "fabric"))
        second = read_artifact(jar(self.root, "neoforge", minecraft="1.21.2"))
        raw = suggested_config(self.root, [first, second])
        self.assertEqual(raw["artifacts"][1]["game_versions"], ["1.21.2"])

    def test_duplicate_release_id_is_rejected(self):
        one = jar(self.root, name="one.jar")
        two = jar(self.root, name="two.jar", payload=b"different")
        cfg = config(self.root)
        cfg.raw["artifacts"] = [str(one.relative_to(self.root)), str(two.relative_to(self.root))]
        with self.assertRaisesRegex(CourierError, "same release ID"):
            artifacts(cfg)


if __name__ == "__main__":
    unittest.main()
