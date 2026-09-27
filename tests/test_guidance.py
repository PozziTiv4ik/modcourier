"""Agents get actionable, consistent instructions for the operation that needs work."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.helpers import config, connectors, jar, service
from modcourier.errors import CourierError
from modcourier.handoff import prepare
from modcourier.inspect import artifacts
from modcourier.planner import build_plan
from modcourier.publication import Publication
from modcourier.recovery import status
from modcourier.runner import execute
from modcourier.state import State, atomic_json


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        jar(self.root)
        self.cfg = config(self.root)
        self.items = artifacts(self.cfg)

    def publish(self, server, selection=None, clients=None):
        state = State(self.root)
        plan = build_plan(self.cfg, self.items, state, selection, clients or connectors(self.cfg, server))
        return plan, execute(self.cfg, self.items, state, plan)

    def read_status(self, server):
        clients = connectors(self.cfg, server)
        with patch("modcourier.recovery.REGISTRY", {name: lambda _, c=client: c for name, client in clients.items()}):
            return status(self.cfg, State(self.root))

    def test_status_detects_changed_compatibility_without_local_jars(self):
        with service() as server:
            self.publish(server)
            self.items[0].path.unlink()
            server.mr_versions[0]["game_versions"] = ["1.20.1"]
            server.cf_files[0]["gameVersions"] = ["1.20.1", "Fabric"]
            result = self.read_status(server)
            self.assertTrue(all(r["status"] == "failed" and not r["verified_now"] for r in result["results"]))
            self.assertEqual([a["code"] for a in result["next_actions"]], ["metadata_conflict"] * 2)
            self.assertTrue(all(a["kind"] == "resolve_conflict" for a in result["next_actions"]))

    def test_status_rejection_has_file_context_and_a_recovery_action(self):
        with service() as server:
            self.publish(server, ["curseforge"])
            server.cf_files[0]["fileStatus"] = 5
            result = self.read_status(server)
            self.assertEqual(result["results"][0]["status"], "rejected")
            action = result["next_actions"][0]
            self.assertEqual(action["code"], "remote_rejected")
            self.assertEqual(action["file_id"], "101")
            self.assertEqual(action["key"], "curseforge:" + self.items[0].key)

    def test_status_explains_an_unrecognized_platform_status(self):
        with service() as server:
            self.publish(server, ["modrinth"])
            server.mr_versions[0]["status"] = "new_unknown_status"
            result = self.read_status(server)
            self.assertEqual(result["results"][0]["status"], "new_unknown_status")
            self.assertEqual(result["next_actions"][0]["code"], "remote_attention")

    def test_uncertain_status_without_a_receipt_instructs_reconciliation(self):
        state = State(self.root)
        for operation in ("create", self.items[0].key, "update_page", "submit"):
            state.record("modrinth:" + operation, status="uncertain")
        with patch("modcourier.http.Http.request", side_effect=AssertionError("No receipt needs a lookup")):
            result = status(self.cfg, state)
        self.assertEqual([a["code"] for a in result["next_actions"]],
                         ["uncertain_creation", "uncertain_upload", "page_unverified", "submission_unverified"])

    def test_page_only_handoff_does_not_tell_the_agent_to_create_projects(self):
        with service() as server:
            server.cf_body = "An externally edited description."
            plan, report = self.publish(server, ["curseforge"])
            action = plan.as_dict()["next_actions"][0]
            self.assertEqual(action["code"], "page_review_required")
            self.assertNotIn("Create a project", " ".join(action["instructions"]))
            self.assertEqual(report["next_actions"], plan.as_dict()["next_actions"])
            directory = State(self.root).directory
            prepare(self.cfg, self.items, plan, directory, report)
            task = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))["tasks"][0]
            task.pop("reason")
            self.assertEqual(task, action)

    def test_pending_moderation_tells_the_agent_to_check_status(self):
        with service() as server:
            _, report = self.publish(server, ["curseforge"])
            self.assertTrue(report["complete"])
            action = report["next_actions"][0]
            self.assertEqual(action["kind"], "verify")
            self.assertEqual(action["file_id"], "101")
            self.assertIn("status --json", " ".join(action["instructions"]))

    def test_missing_catalog_after_acceptance_gives_the_actual_next_step(self):
        self.cfg.settings("curseforge").update(bootstrap_version="1.0.0", page_review={
            "sha256": Publication.read(self.cfg).page_sha256, "remote_sha256": "",
        })
        with service() as server:
            clients = connectors(self.cfg, server)
            clients["curseforge"].key = ""
            _, report = self.publish(server, ["curseforge"], clients)
            self.assertTrue(report["complete"], report)
            self.assertEqual(report["results"][0]["status"], "accepted_unverified")
            self.assertEqual(report["next_actions"][0]["code"], "catalog_unavailable")
            self.assertEqual(server.cf_uploads, 1)
            self.publish(server, ["curseforge"], clients)
            self.assertEqual(server.cf_uploads, 1)

    def test_malformed_journal_status_and_binding_are_actionable(self):
        bad_values = [
            {"operations": {"modrinth:release": {"status": "typo"}}, "projects": {}},
            {"operations": {"modrinth:release": {"status": {}}}, "projects": {}},
            {"operations": {}, "projects": {"modrinth": []}},
        ]
        for value in bad_values:
            with self.subTest(value=value):
                atomic_json(self.root / ".modcourier" / "state.json", {"schema_version": 1, **value})
                with self.assertRaises(CourierError) as caught:
                    State(self.root)
                self.assertEqual(caught.exception.code, "invalid_state")
