"""Publishing scenarios for connector preparation and untrustworthy API replies."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from tests.helpers import config, connectors, jar, service
from modcourier.inspect import artifacts
from modcourier.planner import build_plan
from modcourier.runner import execute
from modcourier.state import State


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        jar(self.root)
        self.cfg = config(self.root)
        self.items = artifacts(self.cfg)

    def publish(self, server, selection=None):
        state = State(self.root)
        plan = build_plan(self.cfg, self.items, state, selection, connectors(self.cfg, server))
        return plan, execute(self.cfg, self.items, state, plan)

    def version(self, **changes):
        artifact = self.items[0]
        return {"id": "v1", "project_id": "mr1", "version_number": artifact.key, "status": "listed",
                "loaders": artifact.loaders, "game_versions": artifact.game_versions,
                "files": [{"filename": artifact.path.name, "hashes": artifact.hashes}], **changes}

    def test_a_prepared_plan_can_execute_with_fresh_connectors(self):
        with service() as server:
            state = State(self.root)
            plan = build_plan(self.cfg, self.items, state, connectors=connectors(self.cfg, server))
            # Execution must not depend on a previous validate call on these instances.
            plan.connectors = connectors(self.cfg, server)
            report = execute(self.cfg, self.items, state, plan)
            self.assertTrue(report["complete"], report)
            self.assertEqual((server.mr_uploads, server.cf_uploads), (1, 1))
            self.assertNotIn("upload_data", json.dumps(plan.as_dict()))

    def test_another_plan_cannot_replace_prepared_release_metadata(self):
        with service() as server:
            state, clients = State(self.root), connectors(self.cfg, server)
            self.cfg.release["type"] = "alpha"
            first = build_plan(self.cfg, self.items, state, connectors=clients)
            self.cfg.release["type"] = "beta"
            build_plan(self.cfg, self.items, state, connectors=clients)
            report = execute(self.cfg, self.items, state, first)
            self.assertTrue(report["complete"], report)
            self.assertEqual(server.mr_versions[0]["version_type"], "alpha")
            self.assertEqual(server.cf_upload_metadata[0]["releaseType"], "alpha")

    def test_auto_discovery_cannot_choose_another_project_type(self):
        self.cfg.settings("modrinth").pop("project_id")
        with service() as server:
            server.mr_project["project_type"] = "resourcepack"
            plan, report = self.publish(server)
            self.assertFalse(report["complete"])
            self.assertEqual(plan.steps[0].details["code"], "wrong_project_type")
            self.assertEqual((server.mr_uploads, server.cf_uploads), (0, 1))

    def test_incomplete_author_projects_do_not_prove_absence(self):
        self.cfg.settings("modrinth").pop("project_id")
        with service() as server:
            server.mr_project = None
            route = server.route
            def incomplete(method, path, headers, raw):
                if path == "/mr/user/user1/projects":
                    return 200, [{}], {}
                return route(method, path, headers, raw)
            server.route = incomplete
            plan, report = self.publish(server, ["modrinth"])
            self.assertFalse(report["complete"])
            self.assertEqual(plan.steps[0].details["code"], "invalid_response")
            self.assertEqual(server.mr_creates, 0)

    def test_malformed_version_lists_never_authorize_upload(self):
        cases = [self.version(files={}), self.version(files=None), self.version(id=None),
                 self.version(id=True), self.version(project_id=None),
                 self.version(files=[{"filename": "demo.jar", "hashes": {"sha512": "invalid"}}])]
        for version in cases:
            with self.subTest(version=version), service() as server:
                server.mr_versions = [version]
                plan, report = self.publish(server, ["modrinth"])
                self.assertFalse(report["complete"])
                self.assertEqual(plan.steps[0].details["code"], "invalid_response")
                self.assertEqual(server.mr_uploads, 0)

    def test_empty_draft_version_requires_attention_instead_of_another_upload(self):
        with service() as server:
            server.mr_versions = [self.version(files=[], status="draft")]
            plan, report = self.publish(server, ["modrinth"])
            self.assertFalse(report["complete"])
            self.assertEqual(plan.steps[0].details["code"], "remote_attention")
            self.assertEqual(server.mr_uploads, 0)

    def test_listing_files_from_another_project_blocks_before_writes(self):
        with service() as server:
            server.mr_versions = [self.version(project_id="anotherProject")]
            plan, report = self.publish(server, ["modrinth"])
            self.assertFalse(report["complete"])
            self.assertEqual(plan.steps[0].details["code"], "verification_failed")
            self.assertTrue(all(method == "GET" for method, _ in server.calls))

    def test_invalid_upload_acknowledgement_cannot_authorize_a_retry(self):
        with service() as server:
            route = server.route
            def malformed(method, path, headers, raw):
                code, data, extra = route(method, path, headers, raw)
                if method == "POST" and path == "/mr/version":
                    data = {**data, "id": True}
                return code, data, extra
            server.route = malformed
            _, first = self.publish(server, ["modrinth"])
            self.assertEqual(first["results"][0]["status"], "uncertain")
            server.mr_versions = []  # The catalog has not exposed the accepted file yet.
            plan, second = self.publish(server, ["modrinth"])
            self.assertFalse(second["complete"])
            self.assertEqual(plan.steps[0].details["code"], "uncertain_upload")
            self.assertEqual(server.mr_uploads, 1)

    def test_rejected_acknowledgement_survives_an_unavailable_followup_read(self):
        with service() as server:
            route = server.route
            def rejected(method, path, headers, raw):
                if method == "GET" and path == "/mr/project/mr1" and server.mr_uploads:
                    return 503, {"message": "catalog unavailable"}, {}
                code, data, extra = route(method, path, headers, raw)
                if method == "POST" and path == "/mr/version":
                    data["status"] = "archived"
                return code, data, extra
            server.route = rejected
            _, report = self.publish(server, ["modrinth"])
            self.assertFalse(report["complete"])
            self.assertEqual(report["results"][0]["code"], "remote_rejected")
            record = State(self.root).operation("modrinth:" + self.items[0].key)
            self.assertEqual(record["status"], "accepted")
            self.assertEqual(record["receipt"]["status"], "archived")

    def test_unknown_remote_status_is_not_success(self):
        with service() as server:
            server.mr_versions = [self.version(status="new_unknown_status")]
            plan, report = self.publish(server, ["modrinth"])
            self.assertFalse(report["complete"])
            self.assertEqual(plan.steps[0].details["code"], "remote_attention")
            self.assertEqual(server.mr_uploads, 0)

    def test_saved_receipt_cannot_hide_a_compatibility_change(self):
        with service() as server:
            self.publish(server, ["modrinth"])
            artifact = self.items[0]
            artifact.release_id = artifact.key
            artifact.game_versions = ["1.20.1"]
            server.mr_versions = []
            plan, report = self.publish(server, ["modrinth"])
            self.assertFalse(report["complete"])
            self.assertEqual(plan.steps[0].details["code"], "metadata_conflict")
            self.assertEqual(server.mr_uploads, 1)

    def test_changed_or_repeated_catalog_pages_cannot_prove_absence(self):
        for change in ("total", "duplicate"):
            with self.subTest(change=change), service() as server:
                server.pages = [
                    {"id": n, "modId": 42, "displayName": f"old-{n}", "fileName": f"old-{n}.jar",
                     "fileStatus": 10, "isAvailable": True, "gameVersions": ["1.21.1", "Fabric"], "hashes": []}
                    for n in range(1, 52)
                ]
                route = server.route
                def unstable(method, path, headers, raw):
                    code, data, extra = route(method, path, headers, raw)
                    if "/cf/v1/mods/42/files?" in path and parse_qs(urlsplit(path).query)["index"] == ["50"]:
                        if change == "total":
                            data = {"data": [], "pagination": {"index": 50, "resultCount": 0, "totalCount": 50}}
                        else:
                            data = {**data, "data": [copy.deepcopy(server.pages[0])]}
                    return code, data, extra
                server.route = unstable
                plan, report = self.publish(server, ["curseforge"])
                self.assertFalse(report["complete"])
                self.assertEqual(plan.steps[0].details["code"], "listing_incomplete")
                self.assertEqual(server.cf_uploads, 0)

    def test_submission_only_does_not_resolve_upload_catalogs_again(self):
        with service() as server:
            self.publish(server, ["modrinth"])
            server.mr_project["status"] = "draft"
            server.calls.clear()
            _, report = self.publish(server, ["modrinth"])
            self.assertTrue(report["complete"], report)
            self.assertEqual(server.mr_uploads, 1)
            self.assertFalse(any("/tag/" in path for _, path in server.calls))

    def test_project_rejected_during_submission_is_not_a_completed_release(self):
        with service() as server:
            server.mr_project["status"] = "draft"
            route, reads_after_upload = server.route, 0
            def rejected(method, path, headers, raw):
                nonlocal reads_after_upload
                if method == "GET" and path == "/mr/project/mr1" and server.mr_uploads:
                    reads_after_upload += 1
                    if reads_after_upload == 2:
                        server.mr_project["status"] = "rejected"
                return route(method, path, headers, raw)
            server.route = rejected
            _, report = self.publish(server)
            self.assertFalse(report["complete"])
            self.assertEqual(report["results"][1]["code"], "project_rejected")
            self.assertEqual((server.mr_uploads, server.cf_uploads), (1, 1))
            self.assertEqual(State(self.root).operation("modrinth:" + self.items[0].key)["status"], "accepted")
