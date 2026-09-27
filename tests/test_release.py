import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tests.helpers import jar

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_pyz_runs_outside_checkout_and_is_reproducible(self):
        spec = importlib.util.spec_from_file_location("release_builder", ROOT / "scripts" / "build_release.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "modcourier.pyz"
            module.build(target)
            first = target.read_bytes()
            module.build(target)
            self.assertEqual(first, target.read_bytes())
            result = subprocess.run([sys.executable, str(target), "--version"], cwd=directory,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ModCourier 1.0.0", result.stdout)
            jar(Path(directory))
            env = {**os.environ, "PYTHONPATH": ""}
            initialized = subprocess.run([sys.executable, str(target), "init", "--json"], cwd=directory,
                                         env=env, capture_output=True, text=True)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            self.assertIn("config", json.loads(initialized.stdout))
            doctor = subprocess.run([sys.executable, str(target), "doctor", "--json"], cwd=directory,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(doctor.returncode, 2, doctor.stderr)
            self.assertIn("next_actions", json.loads(doctor.stdout))
