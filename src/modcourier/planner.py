from dataclasses import dataclass, field
import re

from .connectors import REGISTRY
from .errors import CourierError
from .inspect import local_issues
from .models import RemoteFile, Step, same_file


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    projects: dict = field(default_factory=dict)
    connectors: dict = field(default_factory=dict, repr=False)

    def as_dict(self):
        return {
            "schema_version": 1,
            "ready": not any(s.action in {"blocked", "needs_browser"} for s in self.steps),
            "steps": [s.as_dict() for s in self.steps],
        }


def variant_matches(artifact, remote):
    return (not remote.loaders or set(remote.loaders) == set(artifact.loaders)) and (
        not remote.game_versions or set(remote.game_versions) == set(artifact.game_versions)
    )


def choose_release(artifact, files):
    identical = [f for f in files if same_file(artifact, f)]
    if identical:
        if not any(variant_matches(artifact, f) for f in identical):
            raise CourierError("metadata_conflict", f"{artifact.path.name} exists with different loader/Minecraft tags.")
        remote = next(f for f in identical if variant_matches(artifact, f))
        if remote.status in {"rejected", "deleted", "archived", "failed"}:
            raise CourierError("remote_rejected", f"Matching remote file {remote.id} is {remote.status}; resolve it in the author dashboard.")
        if remote.status in {"draft", "testing", "unknown"}:
            raise CourierError("browser_required", f"Matching remote file {remote.id} is {remote.status}; check its release state in the author dashboard.")
        return remote
    for remote in files:
        version_match = re.search(r"(?<![A-Za-z0-9._+-])v?" + re.escape(artifact.version) + r"(?![A-Za-z0-9._+-])", remote.label)
        label_match = remote.label in {artifact.key, artifact.display_name, artifact.version}
        if remote.filename == artifact.path.name or label_match or (version_match and variant_matches(artifact, remote)):
            # Same release identity without the same bytes must never be overwritten.
            if variant_matches(artifact, remote):
                raise CourierError("version_conflict", f"{artifact.key} already exists with different or unverifiable content (file {remote.id}).")
    return None


def build_plan(config, items, state, selected=None, connectors=None):
    plan = Plan()
    requirements = local_issues(config, items)
    for name in config.enabled(selected):
        connector = (connectors or {}).get(name) or REGISTRY[name](config)
        plan.connectors[name] = connector
        try:
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
            if project and project.status in {"rejected", "withheld", "archived", "deleted"}:
                raise CourierError("project_rejected", f"The project is {project.status}; inspect its moderation messages.")
            create_record = state.operation(name + ":create")
            if project is None and create_record.get("status") in {"started", "uncertain", "accepted"}:
                raise CourierError("uncertain_creation", "A previous project creation may have succeeded. Check drafts and bind its ID before continuing.")
            if project is None and not connector.capabilities["create_project"]:
                raise CourierError("browser_required", "Create or locate the project in the author dashboard, then bind its ID.")
            files = connector.files(project) if project else []
            actions = []
            for artifact in items:
                key = f"{name}:{artifact.key}"
                previous = state.operation(key)
                if previous and previous.get("project_id") and project and str(previous["project_id"]) != project.id:
                    raise CourierError("binding_conflict", f"Journal entry {key} belongs to another remote project.")
                matched = choose_release(artifact, files)
                if matched:
                    actions.append(Step(name, "skip", "The platform already has this file.", project.id, artifact, matched))
                    continue
                if previous.get("status") in {"started", "uncertain", "accepted"} and previous.get("sha256") and previous["sha256"] != artifact.hashes["sha256"]:
                    raise CourierError("version_conflict", f"{artifact.key} changed since a previous upload attempt. Resolve that release first.")
                if previous.get("status") == "accepted" and previous.get("receipt"):
                    receipt = RemoteFile(**previous["receipt"])
                    actions.append(Step(name, "skip", "Upload was acknowledged; public visibility is not yet verified.",
                                        project.id if project else "", artifact, receipt))
                    continue
                if previous.get("status") in {"started", "uncertain"}:
                    raise CourierError("uncertain_upload", f"{key} may have been accepted before the connection failed. Check the author dashboard; see docs/recovery.md.")
                actions.append(Step(name, "upload", "This release file is not present.",
                                    project.id if project else "", artifact))
            uploads = [s.artifact for s in actions if s.action == "upload"]
            if uploads:
                connector.validate(uploads, new=project is None)
            elif name == "modrinth" and project and project.status == "draft":
                # Submission still needs policy checks when every file was uploaded earlier.
                connector.validate(items, new=False)
            if project is None:
                plan.steps.append(Step(name, "create", "Create a new draft project."))
            plan.steps.extend(actions)
            if name == "modrinth" and (project is None or project.status == "draft"):
                plan.steps.append(Step(name, "submit", "Submit the complete project for moderation.",
                                       project.id if project else ""))
        except CourierError as exc:
            plan.steps.append(Step(
                name, "needs_browser" if exc.code == "browser_required" else "blocked",
                str(exc), details={"code": exc.code},
            ))
    return plan
