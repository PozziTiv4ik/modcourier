from dataclasses import dataclass, field
import re

from .connectors import REGISTRY
from .errors import CourierError
from .guidance import next_actions, BROWSER_CODES
from .inspect import local_issues
from .models import ACCEPTED_STATUSES, RemoteFile, Step, check_remote_file, check_variant, same_file, variant_matches
from .publication import reviewed_publication


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    projects: dict = field(default_factory=dict)
    connectors: dict = field(default_factory=dict, repr=False)
    publication_sha256: str = ""

    def as_dict(self):
        return {
            "schema_version": 1,
            "ready": not any(s.action in {"blocked", "needs_browser"} for s in self.steps),
            "steps": [s.as_dict() for s in self.steps],
            "publication_language": "en",
            "next_actions": next_actions([s.as_dict() for s in self.steps]),
        }


def choose_release(artifact, files):
    identical = [f for f in files if same_file(artifact, f)]
    if identical:
        matching = [f for f in identical if variant_matches(artifact, f)]
        if not matching:
            raise CourierError("metadata_conflict", f"{artifact.path.name} exists with different or missing loader/Minecraft tags.")
        remote = next((f for f in matching if f.status in ACCEPTED_STATUSES), matching[0])
        check_remote_file(remote)
        return remote
    for remote in files:
        version_match = re.search(r"(?<![A-Za-z0-9._+-])v?" + re.escape(artifact.version) + r"(?![A-Za-z0-9._+-])", remote.label)
        label_match = remote.label in {artifact.key, artifact.display_name, artifact.version}
        if remote.filename == artifact.path.name or label_match or version_match:
            # Same release identity without the same bytes must never be overwritten.
            if variant_matches(artifact, remote, allow_unknown=True):
                raise CourierError("version_conflict", f"{artifact.key} already exists with different or unverifiable content (file {remote.id}).")
    return None


def plan_release(name, artifact, project, files, state):
    key = f"{name}:{artifact.key}"
    previous = state.operation(key)
    project_id = project.id if project else ""
    if previous.get("project_id") and project and str(previous["project_id"]) != project.id:
        raise CourierError("binding_conflict", f"Journal entry {key} belongs to another remote project.")
    matched = choose_release(artifact, files)
    if matched:
        return Step(name, "skip", "The platform already has this file.", project_id, artifact, matched)
    if (previous.get("status") in {"started", "uncertain", "accepted"}
        and previous.get("sha256") and previous["sha256"] != artifact.hashes["sha256"]):
        raise CourierError("version_conflict", f"{artifact.key} changed since a previous upload attempt. Resolve that release first.")
    if previous.get("status") == "accepted" and previous.get("receipt"):
        receipt = RemoteFile(**previous["receipt"])
        if not same_file(artifact, receipt):
            raise CourierError("verification_failed", f"The saved receipt for {key} does not match the artifact.", uncertain=True)
        check_variant(artifact, receipt)
        check_remote_file(receipt)
        return Step(name, "skip", "Upload was acknowledged; public visibility is not yet verified.",
                    project_id, artifact, receipt)
    if previous.get("status") in {"started", "uncertain"}:
        raise CourierError("uncertain_upload", f"{key} may have been accepted before the connection failed. Check the author dashboard; see docs/recovery.md.")
    return Step(name, "upload", "This release file is not present.", project_id, artifact)


def build_plan(config, items, state, selected=None, connectors=None):
    plan = Plan()
    requirements = local_issues(config, items)
    for name in config.enabled(selected):
        connector = (connectors or {}).get(name) or REGISTRY[name](config)
        plan.connectors[name] = connector
        try:
            publication = reviewed_publication(config)
            plan.publication_sha256 = publication.sha256
            if requirements:
                raise CourierError("release_metadata", " ".join(requirements))
            bound = config.settings(name).get("project_id")
            journal_bound = state.data["projects"].get(name)
            if bound and journal_bound and str(bound) != str(journal_bound):
                raise CourierError("binding_conflict", "Configuration and journal refer to different projects. Use a separate mod directory; do not discard the journal.")
            if not bound and journal_bound:
                connector.settings = {**connector.settings, "project_id": journal_bound}
            project = connector.discover()
            plan.projects[name] = project
            if project and project.status not in {"draft", "approved", "unlisted", "published", "processing", "pending_moderation"}:
                raise CourierError("project_rejected", f"The project is {project.status}; inspect its moderation messages.")
            create_record = state.operation(name + ":create")
            if project is None and create_record.get("status") in {"started", "uncertain", "accepted"}:
                raise CourierError("uncertain_creation", "A previous project creation may have succeeded. Check drafts and bind its ID before continuing.")
            if project is None and not connector.capabilities["create_project"]:
                raise CourierError("browser_required", "Create or locate the project in the author dashboard, then bind its ID.")
            page_action = connector.page_action(project) if project else None
            files = connector.files(project) if project else []
            actions = [plan_release(name, artifact, project, files, state) for artifact in items]
            uploads = [s.artifact for s in actions if s.action == "upload"]
            if uploads or connector.needs_submission(project) or page_action:
                connector.validate_project(new=project is None)
            if uploads:
                prepared = connector.prepare_uploads(uploads)
                for step in actions:
                    if step.action == "upload":
                        step.upload_data = prepared[step.artifact.key]
            if project is None:
                plan.steps.append(Step(name, "create", "Create a new draft project."))
            elif page_action:
                plan.steps.append(Step(name, page_action, "Synchronize the reviewed English project page.", project.id))
            elif project and state.operation(name + ":update_page").get("status") in {"started", "uncertain", "failed"}:
                plan.steps.append(Step(name, "verify_page", "The current page matches the reviewed English copy; reconcile the prior update.", project.id))
            plan.steps.extend(actions)
            if connector.needs_submission(project):
                plan.steps.append(Step(name, "submit", "Submit the complete project for moderation.",
                                       project.id if project else ""))
        except CourierError as exc:
            plan.steps.append(Step(
                name, "needs_browser" if exc.code in BROWSER_CODES else "blocked",
                str(exc), details={"code": exc.code},
            ))
    return plan
