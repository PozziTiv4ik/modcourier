"""Regression scenarios found during the publisher and connector audit."""
import argparse
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tests.helpers import config, connectors, jar, service
from modcourier.cli import main, recover, status
from modcourier.config import Config
from modcourier.connectors.curseforge import CurseForge
from modcourier.errors import CourierError
from modcourier.handoff import prepare
from modcourier.inspect import artifacts, suggested_config
from modcourier.metadata import read_artifact
from modcourier.planner import build_plan
from modcourier.runner import execute
from modcourier.state import State, atomic_json


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        jar(self.root)
        self.cfg = config(self.root)
        self.items = artifacts(self.cfg)

    def publish(self, server, selection=None):
        clients = connectors(self.cfg, server)
        state = State(self.root)
        plan = build_plan(self.cfg, self.items, state, selection, clients)
        return plan, execute(self.cfg, self.items, state, plan)

    def call(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([*arguments, "--project", str(self.root), "--json"])
        return code, json.loads(output.getvalue())

    def test_duplicate_platform_selection_does_not_upload_twice(self):
        with service() as server:
            plan, report = self.publish(server, ["modrinth", "modrinth", "curseforge", "curseforge"])
            self.assertTrue(report["complete"], report)
            self.assertEqual(len(plan.steps), 2)
            self.assertEqual((server.mr_uploads, server.cf_uploads), (1, 1))

    def test_no_enabled_targets_is_not_a_successful_publication(self):
        for name in self.cfg.platforms:
            self.cfg.settings(name)["enabled"] = False
        with self.assertRaisesRegex(CourierError, "No selected platform"):
            build_plan(self.cfg, self.items, State(self.root))

    def test_release_ids_cannot_overwrite_project_journal_entries(self):
        for release_id in ("create", "submit", "update_page", "verify_page"):
            with self.subTest(release_id=release_id):
                self.cfg.raw["artifacts"] = [{"path": str(self.items[0].path), "release_id": release_id}]
                with self.assertRaisesRegex(CourierError, "reserved"):
                    artifacts(self.cfg)

    def test_init_does_not_guess_other_loaders_environment(self):
        fabric = read_artifact(self.items[0].path)
        forge = read_artifact(jar(self.root, "forge"))
        initialized = Config(self.root, suggested_config(self.root, [fabric, forge]))
        inspected = artifacts(initialized)
        self.assertEqual([a.environment for a in inspected], ["client_only", ""])

    def test_universal_jar_requires_agreement_before_inferring_compatibility(self):
        path = self.items[0].path
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("quilt.mod.json", json.dumps({"quilt_loader": {
                "id": "demo", "version": "1.0.0", "metadata": {"license": "MIT"},
                "depends": [{"id": "minecraft", "versions": "1.21.2"}],
            }}))
        artifact = read_artifact(path)
        self.assertEqual(artifact.loaders, ["fabric", "quilt"])
        self.assertEqual(artifact.game_versions, [])
        self.assertEqual(artifact.environment, "")
        self.assertEqual(read_artifact(path, {"game_versions": ["1.21.1"], "environment": "client_only"}).game_versions, ["1.21.1"])

    def test_malformed_descriptor_is_actionable(self):
        path = self.items[0].path
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("fabric.mod.json", "[]")
        with self.assertRaises(CourierError) as error:
            read_artifact(path)
        self.assertEqual(error.exception.code, "invalid_artifact")

    def test_malformed_one_platform_does_not_block_the_other(self):
        with service() as server:
            server.mr_versions = [{}]
            plan, report = self.publish(server)
            self.assertFalse(report["complete"])
            self.assertEqual(plan.steps[0].details["code"], "invalid_response")
            self.assertEqual((server.mr_uploads, server.cf_uploads), (0, 1))

    def test_empty_lookup_response_cannot_authorize_creation(self):
        self.cfg.settings("modrinth").pop("project_id")
        with service() as server:
            server.mr_project = None
            route = server.route
            def empty(method, path, headers, raw):
                if path == "/mr/project/demo":
                    return 200, None, {}
                return route(method, path, headers, raw)
            server.route = empty
            plan, _ = self.publish(server, ["modrinth"])
            self.assertEqual(plan.steps[0].details["code"], "invalid_response")
            self.assertEqual(server.mr_creates, 0)

    def test_modrinth_cannot_publish_a_mod_into_a_resource_pack(self):
        with service() as server:
            server.mr_project["project_type"] = "resourcepack"
            plan, _ = self.publish(server, ["modrinth"])
            self.assertEqual(plan.steps[0].details["code"], "wrong_project_type")
            self.assertEqual(server.mr_uploads, 0)

    def test_unverified_receipt_preserves_uncertainty(self):
        with service() as server:
            self.publish(server, ["modrinth"])
            state = State(self.root)
            state.record("modrinth:" + self.items[0].key, status="uncertain")
            server.mr_project = None
            clients = connectors(self.cfg, server)
            with patch("modcourier.recovery.REGISTRY", {"modrinth": lambda _: clients["modrinth"]}):
                result = status(self.cfg, state)
            self.assertEqual(result["results"][0]["status"], "uncertain")

    def test_invalid_receipt_is_reported_before_remote_operations(self):
        state = State(self.root)
        state.record("modrinth:" + self.items[0].key, status="accepted", receipt={"id": "v1"})
        with self.assertRaises(CourierError) as error:
            State(self.root)
        self.assertEqual(error.exception.code, "invalid_state")

    def test_invalid_project_ids_and_boolean_schema_are_rejected(self):
        for raw in ({"schema_version": True},
                    {"platforms": {"curseforge": {"project_id": True}}},
                    {"platforms": {"curseforge": {"project_id": 0}}},
                    {"platforms": {"curseforge": {"project_id": "\u0664\u0662"}}}):
            with self.subTest(raw=raw), self.assertRaises(CourierError):
                Config(self.root, raw)

    def test_missing_or_inconsistent_pagination_cannot_authorize_upload(self):
        for pagination in (None, {"index": 0, "resultCount": 0, "totalCount": 1},
                           {"index": 50, "resultCount": 0, "totalCount": 50}):
            with self.subTest(pagination=pagination), service() as server:
                route = server.route
                def incomplete(method, path, headers, raw):
                    if "/cf/v1/mods/42/files?" in path:
                        return 200, {"data": [], "pagination": pagination}, {}
                    return route(method, path, headers, raw)
                server.route = incomplete
                plan, _ = self.publish(server, ["curseforge"])
                self.assertEqual(plan.steps[0].details["code"], "listing_incomplete")
                self.assertEqual(server.cf_uploads, 0)

    def test_official_curseforge_statuses_and_download_availability(self):
        with service() as server:
            cf = connectors(self.cfg, server)["curseforge"]
            project = cf.get_project("42")
            data = {"id": 101, "modId": 42, "displayName": "Demo", "fileName": "demo.jar",
                    "hashes": [], "gameVersions": [], "isAvailable": True}
            expected = {4: "published", 5: "rejected", 6: "malware_detected", 7: "deleted",
                        8: "archived", 10: "published", 15: "failed", 18: "pending_moderation",
                        21: "processing"}
            for value, status_name in expected.items():
                self.assertEqual(cf.remote_file({**data, "fileStatus": value}, project).status, status_name)
            self.assertEqual(cf.remote_file({**data, "fileStatus": 10, "isAvailable": False}, project).status, "unavailable")
            self.assertEqual(cf.remote_file({**data, "fileStatus": 10, "isEarlyAccessContent": True}, project).status, "early_access")
            for value, status_name in {2: "changes_required", 5: "rejected", 7: "inactive", 9: "deleted"}.items():
                self.assertEqual(CurseForge.project({**server.cf_project, "status": value}).status, status_name)

    def test_rejection_after_upload_is_not_success_and_retains_receipt(self):
        with service() as server:
            route = server.route
            def rejected(method, path, headers, raw):
                response = route(method, path, headers, raw)
                if path.startswith("/upload/projects/"):
                    server.cf_files[-1]["fileStatus"] = 5
                return response
            server.route = rejected
            _, report = self.publish(server, ["curseforge"])
            self.assertFalse(report["complete"])
            self.assertEqual(report["results"][0]["code"], "remote_rejected")
            record = State(self.root).operation("curseforge:" + self.items[0].key)
            self.assertEqual(record["status"], "accepted")
            self.assertEqual(record["receipt"]["status"], "rejected")
            self.publish(server, ["curseforge"])
            self.assertEqual(server.cf_uploads, 1)

    def test_version_with_multiple_files_verifies_the_matching_file(self):
        with service() as server:
            self.publish(server, ["modrinth"])
            server.mr_versions[0]["files"].insert(0, {
                "filename": "sources.jar", "hashes": {"sha512": "0" * 128, "sha1": "0" * 40},
            })
            _, report = self.publish(server, ["modrinth"])
            self.assertTrue(report["complete"], report)
            self.assertEqual(report["results"][0]["status"], "published")
            self.assertEqual(server.mr_uploads, 1)

    def test_existing_usable_copy_wins_over_archived_identical_copy(self):
        with service() as server:
            self.publish(server, ["modrinth"])
            archived = copy.deepcopy(server.mr_versions[0])
            archived.update(id="archived1", status="archived")
            server.mr_versions.insert(0, archived)
            _, report = self.publish(server, ["modrinth"])
            self.assertTrue(report["complete"], report)
            self.assertEqual(server.mr_uploads, 1)

    def test_wrong_tags_after_upload_do_not_count_as_completed(self):
        with service() as server:
            route = server.route
            def wrong_tags(method, path, headers, raw):
                response = route(method, path, headers, raw)
                if path.startswith("/upload/projects/"):
                    server.cf_files[-1]["gameVersions"] = ["1.20.1", "Forge"]
                return response
            server.route = wrong_tags
            _, report = self.publish(server, ["curseforge"])
            self.assertFalse(report["complete"])
            self.assertEqual(report["results"][0]["code"], "metadata_conflict")
            self.publish(server, ["curseforge"])
            self.assertEqual(server.cf_uploads, 1)

    def test_archived_version_is_not_hidden_by_pending_project_status(self):
        with service() as server:
            self.publish(server, ["modrinth"])
            server.mr_project["status"] = "processing"
            server.mr_versions[0]["status"] = "archived"
            plan, report = self.publish(server, ["modrinth"])
            self.assertEqual(plan.steps[0].details["code"], "remote_rejected")
            self.assertFalse(report["complete"])

    def test_status_uses_strongest_hash_even_if_a_weaker_hash_matches(self):
        with service() as server:
            self.publish(server, ["modrinth"])
            server.mr_versions[0]["files"][0]["hashes"]["sha512"] = "0" * 128
            clients = connectors(self.cfg, server)
            with patch("modcourier.recovery.REGISTRY", {"modrinth": lambda _: clients["modrinth"]}):
                result = status(self.cfg, State(self.root))
            self.assertFalse(result["results"][0]["verified_now"])
            self.assertEqual(result["results"][0]["status"], "uncertain")

    def test_file_id_from_another_project_cannot_recover_an_upload(self):
        with service() as server:
            self.publish(server, ["curseforge"])
            cf = connectors(self.cfg, server)["curseforge"]
            server.cf_files[0]["modId"] = 999
            with self.assertRaises(CourierError) as error:
                cf.find_file(cf.get_project("42"), "101", self.items[0])
            self.assertEqual(error.exception.code, "verification_failed")

    def test_recovery_cannot_clear_uncertain_project_creation(self):
        state = State(self.root)
        state.record("modrinth:create", status="uncertain")
        args = argparse.Namespace(platform="modrinth", release_id="create", absent=True, evidence="Checked")
        with self.assertRaises(CourierError) as error:
            recover(self.cfg, state, args)
        self.assertEqual(error.exception.code, "not_upload")
        self.assertEqual(state.operation("modrinth:create")["status"], "uncertain")

    def test_handoff_contains_runtime_failure_and_is_removed_after_success(self):
        with service() as server:
            server.fail_cf = 503
            plan, report = self.publish(server, ["curseforge"])
            directory = State(self.root).directory
            path = prepare(self.cfg, self.items, plan, directory, report)
            self.assertTrue(Path(path).is_file())
            data = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
            self.assertEqual(data["tasks"][0]["kind"], "reconcile")
            prepare(self.cfg, self.items, plan, directory, {"results": []})
            self.assertFalse((directory / "handoff.md").exists())
            self.assertFalse((directory / "handoff.json").exists())

    def test_missing_token_does_not_instruct_creating_a_project(self):
        with service() as server:
            clients = connectors(self.cfg, server)
            clients["modrinth"].token = ""
            plan = build_plan(self.cfg, self.items, State(self.root), ["modrinth"], clients)
            action = plan.as_dict()["next_actions"][0]
            self.assertEqual(action["kind"], "credentials")
            self.assertNotIn("Create a project", " ".join(action["instructions"]))

    def test_doctor_aggregates_setup_without_network_writes_or_secrets(self):
        self.cfg.settings("modrinth")["token_env"] = "MY_MOD_TOKEN"
        atomic_json(self.cfg.path, self.cfg.raw)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch.dict("os.environ", {"MY_MOD_TOKEN": "never-expose-this-token",
                                      "CURSEFORGE_UPLOAD_TOKEN": "", "CURSEFORGE_API_KEY": ""}):
            with patch("modcourier.http.Http.request", side_effect=AssertionError("doctor used the network")):
                code, data = self.call("doctor")
        self.assertEqual(code, 2)
        self.assertEqual([c["present"] for c in data["credentials"]], [True, False, False])
        self.assertNotIn("never-expose-this-token", json.dumps(data))
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_json_syntax_error_is_still_json(self):
        code, data = self.call("publish", "--platform", "typo")
        self.assertEqual(code, 2)
        self.assertEqual(data["error"]["code"], "invalid_argument")
        self.assertTrue(data["next_actions"])

    def test_receipt_mismatch_cannot_skip_an_upload(self):
        with service() as server:
            self.publish(server, ["modrinth"])
            state = State(self.root)
            key = "modrinth:" + self.items[0].key
            receipt = state.operation(key)["receipt"]
            receipt["hashes"] = {"sha512": "0" * 128}
            state.record(key, receipt=receipt)
            server.mr_versions = []
            plan, report = self.publish(server, ["modrinth"])
            self.assertEqual(plan.steps[0].details["code"], "verification_failed")
            self.assertFalse(report["complete"])
            self.assertEqual(server.mr_uploads, 1)
