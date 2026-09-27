from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from tests.helpers import config, connectors, jar, service
from modcourier.errors import CourierError
from modcourier.inspect import artifacts
from modcourier.planner import build_plan
from modcourier.runner import execute
from modcourier.state import State
from modcourier.publication import Publication


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        jar(self.root)
        self.cfg = config(self.root)
        self.items = artifacts(self.cfg)

    def run_release(self, server, selection=None):
        state = State(self.root)
        clients = connectors(self.cfg, server)
        plan = build_plan(self.cfg, self.items, state, selection, clients)
        with state.lock():
            report = execute(self.cfg, self.items, state, plan)
        return plan, report

    def test_both_platforms_upload_once_and_repeat_is_noop(self):
        with service() as server:
            _, first = self.run_release(server)
            self.assertTrue(first["complete"], first)
            plan, second = self.run_release(server)
            self.assertTrue(second["complete"], second)
            self.assertEqual([s.action for s in plan.steps], ["skip", "skip"])
            self.assertEqual(server.mr_uploads, 1)
            self.assertEqual(server.cf_uploads, 1)
            self.assertEqual(second["results"][1]["status"], "pending_moderation")

    def test_new_modrinth_project_is_submitted_after_file(self):
        self.cfg.raw["platforms"]["modrinth"].pop("project_id")
        with service() as server:
            server.mr_project = None
            plan, report = self.run_release(server, ["modrinth"])
            self.assertEqual([s.action for s in plan.steps], ["create", "upload", "submit"])
            self.assertTrue(report["complete"], report)
            self.assertEqual(server.mr_project["status"], "processing")
            self.assertEqual(server.mr_creates, 1)
            self.run_release(server, ["modrinth"])
            self.assertEqual(server.mr_creates, 1)
            self.assertEqual(server.mr_uploads, 1)

    def test_partial_failure_resumes_only_unfinished_platform(self):
        with service() as server:
            server.fail_cf = 400
            _, report = self.run_release(server)
            self.assertFalse(report["complete"])
            server.fail_cf = None
            _, report = self.run_release(server)
            self.assertTrue(report["complete"], report)
            self.assertEqual(server.mr_uploads, 1)
            self.assertEqual(server.cf_uploads, 1)

    def test_server_accepted_but_response_lost_is_reconciled(self):
        with service() as server:
            server.accept_then_fail_cf = True
            _, first = self.run_release(server)
            self.assertFalse(first["complete"])
            self.assertEqual(first["results"][-1]["status"], "uncertain")
            _, second = self.run_release(server)
            self.assertTrue(second["complete"], second)
            self.assertEqual(server.cf_uploads, 1)

    def test_uncertain_without_remote_evidence_blocks_retry(self):
        with service() as server:
            server.fail_cf = 503
            self.run_release(server)
            server.fail_cf = None
            plan, report = self.run_release(server)
            self.assertFalse(report["complete"])
            self.assertIn("uncertain_upload", [s.details.get("code") for s in plan.steps])
            self.assertEqual(server.cf_uploads, 0)

    def test_changed_content_same_version_is_blocked(self):
        with service() as server:
            self.run_release(server)
            jar(self.root, payload=b"new binary same version")
            self.items = artifacts(self.cfg)
            plan, report = self.run_release(server)
            self.assertFalse(report["complete"])
            self.assertTrue(all(s.details.get("code") == "version_conflict" for s in plan.steps))
            self.assertEqual(server.mr_uploads, 1)
            self.assertEqual(server.cf_uploads, 1)

    def test_changed_file_between_plan_and_upload_is_blocked(self):
        with service() as server:
            state = State(self.root)
            plan = build_plan(self.cfg, self.items, state, connectors=connectors(self.cfg, server))
            self.items[0].path.write_bytes(b"replaced")
            report = execute(self.cfg, self.items, state, plan)
            self.assertFalse(report["complete"])
            self.assertEqual(server.mr_uploads + server.cf_uploads, 0)

    def test_multiple_loaders_published_as_distinct_variants(self):
        jar(self.root, "neoforge")
        self.items = artifacts(self.cfg)
        with service() as server:
            _, report = self.run_release(server)
            self.assertTrue(report["complete"], report)
            self.assertEqual(server.mr_uploads, 2)
            self.assertEqual(server.cf_uploads, 2)
            self.assertEqual(len({v["version_number"] for v in server.mr_versions}), 2)

    def test_public_cf_search_cannot_prove_no_draft(self):
        self.cfg.raw["platforms"]["curseforge"].pop("project_id")
        with service() as server:
            plan, report = self.run_release(server, ["curseforge"])
            self.assertEqual(plan.steps[0].action, "needs_browser")
            self.assertEqual(server.cf_uploads, 0)

    def test_new_empty_cf_binding_allows_first_upload_then_no_duplicates(self):
        self.cfg.settings("curseforge")["bootstrap_version"] = "1.0.0"
        self.cfg.settings("curseforge")["page_review"] = {
            "sha256": Publication.read(self.cfg).page_sha256, "remote_sha256": "",
        }
        with service() as server:
            server.cf_visible = False
            _, report = self.run_release(server, ["curseforge"])
            self.assertTrue(report["complete"], report)
            _, report = self.run_release(server, ["curseforge"])
            self.assertTrue(report["complete"], report)
            self.assertEqual(server.cf_uploads, 1)
            self.assertEqual(report["results"][0]["status"], "accepted_unverified")

    def test_ai_primary_blocks_modrinth_only(self):
        self.cfg.raw["policy"]["ai_usage"] = "primary"
        with service() as server:
            _, report = self.run_release(server)
            self.assertFalse(report["complete"])
            self.assertEqual(server.mr_uploads, 0)
            self.assertEqual(server.cf_uploads, 1)

    def test_unmapped_dependency_blocks_before_any_write(self):
        jar(self.root, deps={"custom-library": "*"})
        self.items = artifacts(self.cfg)
        with service() as server:
            plan, report = self.run_release(server)
            self.assertFalse(report["complete"])
            self.assertEqual(server.mr_uploads + server.cf_uploads, 0)
            self.assertEqual({s.details.get("code") for s in plan.steps}, {"dependency_mapping"})

    def test_listing_all_pages(self):
        with service() as server:
            clients = connectors(self.cfg, server)
            server.pages = [
                {"id": n, "displayName": f"old-{n}", "fileName": f"old-{n}.jar", "fileStatus": 6,
                 "gameVersions": ["1.21.1", "Fabric"], "hashes": []} for n in range(121)
            ]
            cf = clients["curseforge"]
            self.assertEqual(len(cf.files(cf.get_project("42"))), 121)
            self.assertEqual(len([p for _, p in server.calls if "/files?" in p]), 3)

    def test_lock_prevents_two_publishers(self):
        state = State(self.root)
        with state.lock():
            with self.assertRaisesRegex(CourierError, "Another publish"):
                with State(self.root).lock():
                    self.fail("Second writer acquired the lock")

    def test_config_and_state_cannot_switch_project(self):
        state = State(self.root)
        state.bind("curseforge", "999")
        with service() as server:
            plan, _ = self.run_release(server, ["curseforge"])
            self.assertEqual(plan.steps[0].details["code"], "binding_conflict")

    def test_dry_plan_writes_nothing(self):
        with service() as server:
            state = State(self.root)
            plan = build_plan(self.cfg, self.items, state, connectors=connectors(self.cfg, server))
            self.assertTrue(plan.as_dict()["ready"])
            self.assertFalse((self.root / ".modcourier").exists())
            self.assertTrue(all(method == "GET" for method, _ in server.calls))

    def test_stable_release_does_not_collide_with_prerelease(self):
        self.items[0].path.unlink()
        jar(self.root, version="1.0.0-beta")
        self.items = artifacts(self.cfg)
        with service() as server:
            _, report = self.run_release(server)
            self.assertTrue(report["complete"], report)
            self.items[0].path.unlink()
            jar(self.root, version="1.0.0", payload=b"stable")
            self.items = artifacts(self.cfg)
            _, report = self.run_release(server)
            self.assertTrue(report["complete"], report)
            self.assertEqual(server.mr_uploads, 2)
            self.assertEqual(server.cf_uploads, 2)

    def test_definitively_failed_upload_can_use_corrected_build(self):
        with service() as server:
            server.fail_cf = 400
            self.run_release(server, ["curseforge"])
            server.fail_cf = None
            jar(self.root, payload=b"fixed")
            self.items = artifacts(self.cfg)
            _, report = self.run_release(server, ["curseforge"])
            self.assertTrue(report["complete"], report)
            self.assertEqual(server.cf_uploads, 1)
