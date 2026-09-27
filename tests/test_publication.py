from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from tests.helpers import config, connectors, jar, review_copy, service
from modcourier.cli import main
from modcourier.config import Config
from modcourier.errors import CourierError
from modcourier.handoff import prepare
from modcourier.inspect import artifacts, suggested_config
from modcourier.planner import build_plan
from modcourier.publication import Publication
from modcourier.runner import execute
from modcourier.state import State, atomic_json

NON_ENGLISH = "\u041c\u043e\u0434 \u0434\u043e\u0431\u0430\u0432\u043b\u044f\u0435\u0442 \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u044b."


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        jar(self.root)
        self.cfg = config(self.root)
        self.items = artifacts(self.cfg)

    def run_release(self, server):
        clients = connectors(self.cfg, server)
        state = State(self.root)
        plan = build_plan(self.cfg, self.items, state, connectors=clients)
        return plan, execute(self.cfg, self.items, state, plan)

    def test_non_english_copy_never_reaches_either_platform(self):
        for section, field in (("project", "title"), ("project", "summary"),
                               ("project", "body"), ("release", "changelog")):
            with self.subTest(field=field), service() as server:
                self.cfg = config(self.root)
                self.cfg.raw[section][field] = NON_ENGLISH
                review_copy(self.cfg)  # Even a forged/stale review cannot pass the script check.
                plan, report = self.run_release(server)
                self.assertFalse(report["complete"])
                self.assertEqual({s.details.get("code") for s in plan.steps}, {"publication_translation_required"})
                self.assertFalse(server.calls)

    def test_latin_text_is_not_automatically_assumed_english(self):
        self.cfg.project["body"] = "Este mod agrega nuevos objetos al juego."
        self.cfg.raw.pop("publication")
        with service() as server:
            plan, _ = self.run_release(server)
            self.assertEqual({s.details.get("code") for s in plan.steps}, {"publication_review_required"})
            self.assertFalse(server.calls)

    def test_legacy_configuration_requires_review(self):
        self.cfg.raw.pop("publication")
        with service() as server:
            plan, _ = self.run_release(server)
            self.assertFalse(plan.as_dict()["ready"])
            self.assertFalse(server.calls)

    def test_language_cannot_be_overridden(self):
        self.cfg.raw["publication"]["language"] = "ru"
        with self.assertRaisesRegex(CourierError, "must use English"):
            Config(self.root, self.cfg.raw)

    def test_file_content_change_invalidates_review(self):
        path = self.root / "description.en.md"
        path.write_text("An English description.", encoding="utf-8")
        self.cfg.project.pop("body")
        self.cfg.project["body_file"] = path.name
        review_copy(self.cfg)
        path.write_text("An edited English description.", encoding="utf-8")
        with service() as server:
            plan, _ = self.run_release(server)
            self.assertEqual({s.details.get("code") for s in plan.steps}, {"publication_review_required"})
            self.assertFalse(server.calls)

    def test_changed_copy_after_plan_is_not_sent(self):
        with service() as server:
            state = State(self.root)
            plan = build_plan(self.cfg, self.items, state, connectors=connectors(self.cfg, server))
            self.cfg.release["changelog"] = "Different release notes."
            review_copy(self.cfg)
            report = execute(self.cfg, self.items, state, plan)
            self.assertFalse(report["complete"])
            self.assertTrue(all(method == "GET" for method, _ in server.calls))

    def test_localized_jar_title_does_not_leak_into_release_name(self):
        self.items[0].title = NON_ENGLISH
        with service() as server:
            _, report = self.run_release(server)
            self.assertTrue(report["complete"], report)
            self.assertTrue(server.mr_versions[0]["name"].startswith("Demo "))
            self.assertTrue(server.cf_upload_metadata[0]["displayName"].startswith("Demo "))
            self.assertEqual(server.cf_upload_metadata[0]["changelog"], "Fix a bug.")

    def test_existing_modrinth_page_is_translated_before_upload(self):
        with service() as server:
            server.mr_project.update(title=NON_ENGLISH, description=NON_ENGLISH, body=NON_ENGLISH)
            plan, report = self.run_release(server)
            self.assertTrue(report["complete"], report)
            self.assertEqual(plan.steps[0].action, "update_page")
            self.assertEqual(server.mr_project["body"], self.cfg.project["body"])
            self.assertEqual(server.page_updates, 1)
            self.run_release(server)
            self.assertEqual(server.page_updates, 1)

    def test_page_update_with_lost_response_reconciles_without_duplicate(self):
        with service() as server:
            server.mr_project["body"] = NON_ENGLISH
            server.lose_page_response = True
            _, first = self.run_release(server)
            self.assertFalse(first["complete"])
            _, second = self.run_release(server)
            self.assertTrue(second["complete"], second)
            self.assertEqual(server.page_updates, 1)
            self.assertEqual(server.mr_uploads, 1)
            self.assertEqual(State(self.root).operation("modrinth:update_page")["status"], "accepted")

    def test_existing_curseforge_page_requires_browser_translation(self):
        with service() as server:
            server.cf_body = NON_ENGLISH
            plan, report = self.run_release(server)
            self.assertFalse(report["complete"])
            self.assertEqual(server.cf_uploads, 0)
            self.assertEqual(server.mr_uploads, 1)
            self.assertEqual(plan.steps[-1].action, "needs_browser")
            self.assertIn("--page-confirmed", plan.steps[-1].reason)

    def test_curseforge_html_review_is_scoped_to_local_and_remote_copy(self):
        with service() as server:
            server.cf_body = "<p>A complete test description.</p>"
            cf = connectors(self.cfg, server)["curseforge"]
            self.cfg.settings("curseforge")["page_review"] = cf.confirm_page("42")
            _, first = self.run_release(server)
            self.assertTrue(first["complete"], first)
            server.cf_body = "<p>An externally edited description.</p>"
            plan, report = self.run_release(server)
            self.assertFalse(report["complete"])
            self.assertEqual(plan.steps[-1].action, "needs_browser")

    def test_curseforge_page_review_cannot_confirm_untranslated_remote(self):
        with service() as server:
            server.cf_body = NON_ENGLISH
            with self.assertRaisesRegex(CourierError, "Translate body"):
                connectors(self.cfg, server)["curseforge"].confirm_page("42")

    def test_handoff_blocks_unreviewed_copy_and_includes_english_release_names(self):
        with service() as server:
            self.cfg.settings("curseforge").pop("project_id")
            state = State(self.root)
            plan = build_plan(self.cfg, self.items, state, connectors=connectors(self.cfg, server))
            prepare(self.cfg, self.items, plan, state.directory)
            data = json.loads((state.directory / "handoff.json").read_text(encoding="utf-8"))
            self.assertEqual(data["publication"]["language"], "en")
            self.assertTrue(data["publication"]["reviewed"])
            self.assertTrue(data["artifacts"][0]["release_name"].startswith("Demo "))
            self.cfg.release["changelog"] = NON_ENGLISH
            plan = build_plan(self.cfg, self.items, state, connectors=connectors(self.cfg, server))
            prepare(self.cfg, self.items, plan, state.directory)
            data = json.loads((state.directory / "handoff.json").read_text(encoding="utf-8"))
            self.assertFalse(data["publication"]["reviewed"])
            self.assertIsNone(data["artifacts"][0]["release_name"])
            self.assertIn("Do not paste", " ".join(data["tasks"][0]["instructions"]))

    def test_review_language_command_records_exact_text(self):
        self.cfg.raw.pop("publication")
        atomic_json(self.cfg.path, self.cfg.raw)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["review-language", "--language", "en", "--project", str(self.root), "--json"])
        self.assertEqual(code, 0, output.getvalue())
        saved = Config(self.root)
        self.assertEqual(saved.raw["publication"]["reviewed_sha256"], Publication.read(saved).sha256)
        self.assertTrue(json.loads(output.getvalue())["publication"]["reviewed"])

    def test_review_language_command_does_not_translate_russian(self):
        self.cfg.project["body"] = NON_ENGLISH
        atomic_json(self.cfg.path, self.cfg.raw)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["review-language", "--language", "en", "--project", str(self.root), "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "publication_translation_required")

    def test_english_typography_unicode_urls_and_literal_code_are_preserved(self):
        self.cfg.project["body"] = (
            "Adds a caf\u00e9 with players\u2019 items \u2014 enjoy! "
            "[Guide](https://example.com/" + NON_ENGLISH.replace(" ", "") + ")\n\n"
            "Literal command: `" + NON_ENGLISH + "`\n\n"
            "```text\n" + NON_ENGLISH + "\n```"
        )
        review_copy(self.cfg)
        self.assertEqual(Publication.read(self.cfg).validate(self.cfg).body, self.cfg.project["body"])

    def test_non_english_link_labels_and_image_alt_need_translation(self):
        for body in ("[" + NON_ENGLISH + "](https://example.com)",
                     '<img src="https://example.com/a.png" alt="' + NON_ENGLISH + '">'):
            self.cfg.project["body"] = body
            with self.assertRaises(CourierError):
                Publication.read(self.cfg).validate(self.cfg, require_review=False)

    def test_init_prefers_english_files_and_never_marks_them_reviewed(self):
        for name in ("README.md", "README.en.md", "CHANGELOG.md", "CHANGELOG.en.md"):
            (self.root / name).write_text("Example content.", encoding="utf-8")
        raw = suggested_config(self.root, self.items)
        self.assertEqual(raw["project"]["body_file"], "README.en.md")
        self.assertEqual(raw["release"]["changelog_file"], "CHANGELOG.en.md")
        self.assertEqual(raw["publication"], {"language": "en", "reviewed_sha256": ""})
