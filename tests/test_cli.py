from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import jar
from modcourier.cli import main

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        jar(self.root)

    def call(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([*args, "--project", str(self.root), "--json"])
        return code, json.loads(output.getvalue())

    def test_init_inspect_and_status(self):
        code, data = self.call("init")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["config"]["project"]["mod_id"], "demo")
        self.assertIn(".modcourier/", (self.root / ".gitignore").read_text())
        code, data = self.call("inspect", "--offline")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["artifacts"][0]["loaders"], ["fabric"])
        self.assertEqual(self.call("status")[0], 0)

    def test_init_does_not_overwrite_config(self):
        self.call("init")
        original = (self.root / "modcourier.json").read_bytes()
        self.assertEqual(self.call("init")[0], 2)
        self.assertEqual((self.root / "modcourier.json").read_bytes(), original)

    def test_new_binding_records_release_scope(self):
        self.call("init")
        code, data = self.call("bind", "curseforge", "42", "--new", "--slug", "demo")
        self.assertEqual(code, 0, data)
        config = json.loads((self.root / "modcourier.json").read_text())
        self.assertEqual(config["platforms"]["curseforge"]["bootstrap_version"], "1.0.0")

    def test_missing_token_emits_actionable_json_without_secret(self):
        self.call("init")
        path = self.root / "modcourier.json"
        raw = json.loads(path.read_text())
        raw["release"]["changelog"] = "A change"
        raw["policy"]["ai_usage"] = "none"
        path.write_text(json.dumps(raw))
        with patch.dict("os.environ", {"MODRINTH_TOKEN": ""}):
            code, data = self.call("inspect", "--platform", "modrinth")
        self.assertEqual(code, 2)
        self.assertEqual(data["steps"][0]["details"]["code"], "missing_token")

    def test_actual_entry_point_runs_without_installation(self):
        result = subprocess.run([sys.executable, str(ROOT / "modcourier.py"), "--version"],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1.0.0", result.stdout)

    def test_options_before_subcommand(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--project", str(self.root), "--json", "inspect", "--offline"])
        self.assertEqual(code, 0)
        self.assertIn("artifacts", json.loads(output.getvalue()))
