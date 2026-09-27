import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import config, connectors, jar, service
from modcourier.cli import recover, status
from modcourier.errors import CourierError
from modcourier.inspect import artifacts
from modcourier.planner import build_plan
from modcourier.runner import execute
from modcourier.state import State, atomic_json


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        jar(self.root)
        self.cfg = config(self.root)
        self.items = artifacts(self.cfg)

    def test_explicit_absence_recovery_preserves_history(self):
        state = State(self.root)
        key = "curseforge:" + self.items[0].key
        state.record(key, status="uncertain", sha256=self.items[0].hashes["sha256"], project_id="42")
        args = argparse.Namespace(platform="curseforge", release_id=self.items[0].key,
                                  absent=True, evidence="Author Files tab checked")
        recover(self.cfg, state, args)
        saved = State(self.root).operation(key)
        self.assertEqual(saved["status"], "failed")
        self.assertTrue(saved["recovery"]["absent"])
        self.assertEqual(saved["sha256"], self.items[0].hashes["sha256"])

    def test_file_id_recovery_verifies_remote_bytes(self):
        with service() as server:
            clients = connectors(self.cfg, server)
            state = State(self.root)
            server.accept_then_fail_cf = True
            plan = build_plan(self.cfg, self.items, state, ["curseforge"], clients)
            execute(self.cfg, self.items, state, plan)
            args = argparse.Namespace(platform="curseforge", release_id=self.items[0].key,
                                      absent=False, file_id="101", evidence="Author Files tab checked")
            with patch("modcourier.cli.REGISTRY", {"curseforge": lambda _: clients["curseforge"]}):
                recover(self.cfg, state, args)
            self.assertEqual(state.operation("curseforge:" + self.items[0].key)["status"], "accepted")

    def test_status_can_verify_without_local_jars(self):
        with service() as server:
            clients = connectors(self.cfg, server)
            state = State(self.root)
            plan = build_plan(self.cfg, self.items, state, connectors=clients)
            execute(self.cfg, self.items, state, plan)
            self.items[0].path.unlink()
            server.cf_files[0]["fileStatus"] = 10
            with patch("modcourier.cli.REGISTRY", {name: (lambda _, c=c: c) for name, c in clients.items()}):
                result = status(self.cfg, state)
            self.assertTrue(all(r["verified_now"] for r in result["results"]))
            self.assertTrue(all(r["status"] == "published" for r in result["results"]))

    def test_invalid_journal_is_actionable(self):
        atomic_json(self.root / ".modcourier" / "state.json", [])
        with self.assertRaises(CourierError):
            State(self.root)

    def test_source_mismatch_blocks_writes(self):
        self.cfg.project["source_url"] = "https://github.com/author/another-mod"
        with service() as server:
            clients = connectors(self.cfg, server)
            plan = build_plan(self.cfg, self.items, State(self.root), connectors=clients)
            self.assertEqual({s.details.get("code") for s in plan.steps}, {"source_mismatch"})
            self.assertTrue(all(method == "GET" for method, _ in server.calls))

    def test_failed_read_does_not_create_a_project(self):
        self.cfg.settings("modrinth").pop("project_id")
        with service() as server:
            clients = connectors(self.cfg, server)
            with patch.object(clients["modrinth"], "discover", side_effect=CourierError("network", "Lookup failed")):
                plan = build_plan(self.cfg, self.items, State(self.root), ["modrinth"], clients)
            self.assertEqual([s.action for s in plan.steps], ["blocked"])
            self.assertEqual(server.mr_creates, 0)

    def test_no_token_values_are_stored_in_error_receipts(self):
        with service() as server:
            clients = connectors(self.cfg, server)
            state = State(self.root)
            server.fail_cf = 401
            plan = build_plan(self.cfg, self.items, state, ["curseforge"], clients)
            execute(self.cfg, self.items, state, plan)
            saved = state.path.read_text()
            self.assertNotIn("test-upload", saved)
            self.assertNotIn("test-key", saved)

    def test_draft_submission_does_not_bypass_policy_after_upload(self):
        with service() as server:
            clients = connectors(self.cfg, server)
            state = State(self.root)
            plan = build_plan(self.cfg, self.items, state, ["modrinth"], clients)
            execute(self.cfg, self.items, state, plan)
            server.mr_project["status"] = "draft"
            self.cfg.raw["policy"]["ai_usage"] = "primary"
            plan = build_plan(self.cfg, self.items, state, ["modrinth"], clients)
            self.assertEqual([s.action for s in plan.steps], ["blocked"])
            self.assertEqual(plan.steps[0].details["code"], "policy_blocked")

    def test_old_published_receipt_is_not_a_current_verification(self):
        with service() as server:
            clients = connectors(self.cfg, server)
            state = State(self.root)
            plan = build_plan(self.cfg, self.items, state, ["modrinth"], clients)
            execute(self.cfg, self.items, state, plan)
            server.mr_project = None
            with patch("modcourier.cli.REGISTRY", {"modrinth": lambda _: clients["modrinth"]}):
                result = status(self.cfg, state)
            self.assertEqual(result["results"][0]["status"], "accepted_unverified")
            self.assertEqual(result["results"][0]["last_known_status"], "published")
            self.assertFalse(result["results"][0]["verified_now"])
