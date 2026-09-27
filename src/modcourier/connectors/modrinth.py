import os
import re

from .base import Connector, api_operation, identifier, records, segment
from ..config import local_path, repo_url
from ..errors import CourierError
from ..http import Http
from ..models import RemoteFile, RemoteProject, file_hashes, same_file


class Modrinth(Connector):
    name = "modrinth"
    credentials = (("token_env", "MODRINTH_TOKEN", "Modrinth author discovery and publishing", False),)
    capabilities = {"create_project": True, "upload": True, "discover": True, "page_translations": False}

    def __init__(self, config, http=None):
        super().__init__(config)
        self.token = os.environ.get(self.settings.get("token_env", "MODRINTH_TOKEN"), "")
        self.http = http or Http("https://api.modrinth.com/v2", {"Authorization": self.token} if self.token else {})
        self.user = None

    def authenticated(self):
        if not self.token:
            raise CourierError("missing_token", "Set MODRINTH_TOKEN (or configured token_env) in the local environment.")
        if self.user is None:
            user = self.http.get("/user")
            identifier(user["id"])
            self.user = user
        return self.user

    @staticmethod
    def project(data):
        if data["project_type"] != "mod":
            raise CourierError("wrong_project_type", "The Modrinth project must be a Minecraft Java mod.")
        return RemoteProject(
            identifier(data["id"]), data["slug"], data["title"], data["status"],
            "https://modrinth.com/mod/" + data["slug"], data.get("source_url") or "", data,
        )

    @api_operation()
    def get_project(self, project_id):
        data = self.http.get("/project/" + segment(project_id), missing_ok=True)
        if data is None:
            return None
        project = self.project(data)
        if str(project_id) not in {project.id, project.slug}:
            raise CourierError("invalid_response", "Modrinth returned a different project ID or slug.")
        return project

    @api_operation()
    def discover(self):
        user = self.authenticated()
        project_id = self.settings.get("project_id")
        owned = records(self.http.get("/user/" + segment(user["id"]) + "/projects"))
        for entry in owned:
            identifier(entry["id"])
            if any(not isinstance(entry.get(field), str) or not entry[field]
                   for field in ("slug", "title", "status", "project_type")):
                raise CourierError("invalid_response", "Modrinth returned incomplete author project data.")
        if project_id:
            project = self.get_project(project_id)
            if project is None:
                raise CourierError("project_inaccessible", "Bound Modrinth project is missing or inaccessible; do not recreate it.")
            self.check_source(project)
            # Explicit binding supports organization/team projects too; the API enforces write scopes.
            if not any(identifier(p["id"]) == project.id for p in owned):
                members = records(self.http.get("/project/" + segment(project.id) + "/members"))
                if not any(identifier(m["user"]["id"]) == user["id"] and m.get("accepted") is True for m in members):
                    raise CourierError("ownership", "The Modrinth token user is not an accepted member of the bound project.")
            return project
        source = repo_url(self.config.project.get("source_url", ""))
        slug = self.config.project.get("slug", "")
        title = self.config.project.get("title", "")
        exact = [p for p in owned if source and repo_url(p.get("source_url") or "") == source
                 and (p.get("slug") == slug or p.get("title") == title)]
        if len(exact) == 1:
            return self.project(exact[0])
        candidates = [p for p in owned if p.get("slug") == slug or p.get("title") == title
                      or (source and repo_url(p.get("source_url") or "") == source)]
        if exact or candidates:
            raise CourierError("ambiguous_project", "Modrinth has possible existing projects. Verify and bind the correct project ID.")
        if not slug:
            raise CourierError("missing_slug", "Set project.slug before discovering or creating a project.")
        existing = self.get_project(slug)
        if existing:
            raise CourierError("slug_taken", f"Modrinth slug {slug} already exists. Verify ownership and bind, or choose another slug.")
        return None

    @api_operation()
    def files(self, project):
        return self.remote_files(self.http.get("/project/" + segment(project.id) + "/version"), project)

    @api_operation()
    def files_for(self, project, file_id):
        version = self.http.get("/version/" + segment(file_id), missing_ok=True)
        if version is None:
            return []
        if identifier(version["id"]) != str(file_id):
            raise CourierError("verification_failed", "Modrinth returned a version from another project or ID.", uncertain=True)
        return self.remote_files([version], project)

    @staticmethod
    def remote_files(versions, project):
        result = []
        seen = set()
        for version in records(versions):
            version_id = identifier(version["id"])
            if identifier(version["project_id"]) != project.id:
                raise CourierError("verification_failed", "Modrinth returned a version from another project.", uncertain=True)
            if version_id in seen:
                raise CourierError("listing_incomplete", "Modrinth repeated a version in its listing; inspect again.")
            seen.add(version_id)
            files = records(version["files"])
            if not files:
                raise CourierError("remote_attention", f"Modrinth version {version_id} has no files. Resolve the empty draft in the author dashboard.")
            for file in files:
                status = version.get("status", "unknown")
                if status in {"listed", "unlisted"}:
                    if project.status == "draft":
                        status = "uploaded"
                    elif project.status in {"approved", "unlisted"}:
                        status = "published"
                    else:
                        status = "pending_moderation" if project.status == "processing" else project.status
                result.append(RemoteFile(
                    version_id, version["version_number"], file["filename"], file["hashes"],
                    status, f"{project.url}/version/{version['id']}",
                    version.get("loaders", []), version.get("game_versions", []),
                ))
        return result

    @api_operation()
    def validate_project(self, *, new=False):
        self.publication()
        self.authenticated()
        policy = self.config.raw.get("policy", {}).get("ai_usage", "unknown")
        if policy == "primary":
            raise CourierError("policy_blocked", "Modrinth prohibits public projects primarily generated by AI. See docs/setup.md.")
        if policy not in {"none", "assisted", "substantial"}:
            raise CourierError("policy_input", "Set policy.ai_usage truthfully: none, assisted, substantial or primary.")
        if policy == "substantial" and not self.settings.get("disclosures_confirmed"):
            raise CourierError("disclosures_required", "Apply the required Modrinth AI disclosures in the project settings, then confirm them with bind --disclosures-confirmed.")
        if new:
            values = self.config.project
            for key in ("title", "summary", "slug", "license"):
                if not isinstance(values.get(key), str) or not values[key].strip():
                    raise CourierError("project_metadata", f"Set project.{key} before creating the Modrinth project.")
            if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", values["slug"]):
                raise CourierError("project_metadata", "project.slug must have 3-64 letters, digits, hyphens or underscores.")
            if len(values["summary"]) > 256:
                raise CourierError("project_metadata", "project.summary exceeds 256 characters.")
            if not self.config.text("project", "body").strip():
                raise CourierError("project_metadata", "Provide project.body or project.body_file.")
            licenses = {entry["short"] for entry in records(self.http.get("/tag/license"))} | {"ARR", "LicenseRef-Custom"}
            if values["license"] not in licenses:
                raise CourierError("project_metadata", "Choose a valid SPDX license, ARR, or LicenseRef-Custom with license_url.")
            if values["license"] == "LicenseRef-Custom" and not values.get("license_url"):
                raise CourierError("project_metadata", "A custom license requires project.license_url.")
            categories = self.settings.get("categories", [])
            known = {entry["name"] for entry in records(self.http.get("/tag/category")) if entry.get("project_type") == "mod"}
            if not categories or not set(categories).issubset(known) or len(categories) > 3:
                raise CourierError("project_metadata", "Choose 1-3 valid modrinth.categories from /tag/category.")
            if values.get("icon"):
                icon = local_path(self.config.root, values["icon"])
                if not icon.is_file():
                    raise CourierError("project_metadata", "project.icon file is missing.")

    @api_operation()
    def prepare_uploads(self, artifacts):
        publication = self.publication()
        known_versions = {v["version"] for v in records(self.http.get("/tag/game_version"))}
        known_loaders = {v["name"] for v in records(self.http.get("/tag/loader"))}
        prepared, resolved = {}, {}
        for artifact in artifacts:
            if set(artifact.game_versions) - known_versions:
                raise CourierError("unsupported_version", f"Modrinth does not recognize {artifact.game_versions}.")
            if set(artifact.loaders) - known_loaders:
                raise CourierError("unsupported_loader", f"Modrinth does not recognize {artifact.loaders}.")
            dependencies = []
            for target, kind in self.dependency_specs(artifact):
                if target not in resolved:
                    resolved[target] = self.get_project(target)
                project = resolved[target]
                if not project:
                    raise CourierError("missing_dependency", f"Modrinth dependency {target} is missing or inaccessible.")
                dependencies.append({"project_id": project.id, "dependency_type": kind})
            prepared[artifact.key] = {
                "name": publication.release_name(artifact), "version_number": artifact.key,
                "changelog": publication.changelog, "dependencies": dependencies,
                "game_versions": list(artifact.game_versions), "loaders": list(artifact.loaders),
                "version_type": self.config.release.get("type", "release"), "environment": artifact.environment,
                "featured": True, "file_parts": ["file"], "primary_file": "file",
            }
        return prepared

    @api_operation(write=True)
    def create_project(self, artifacts):
        publication = self.publication()
        values = self.config.project
        data = {
            "slug": values["slug"], "title": publication.title, "description": publication.summary,
            "body": publication.body, "categories": self.settings["categories"],
            "license_id": values["license"], "license_url": values.get("license_url"),
            "project_type": "mod", "initial_versions": [], "is_draft": True,
            "client_side": "optional", "server_side": "optional",
        }
        for field in ("source_url", "issues_url", "wiki_url", "discord_url"):
            if values.get(field):
                data[field] = values[field]
        files = []
        if values.get("icon"):
            icon = local_path(self.config.root, values["icon"])
            files.append(("icon", icon, file_hashes(icon)["sha256"]))
        response = self.http.multipart("/project", {"data": data}, files)
        if not isinstance(response, dict) or not response.get("id"):
            raise CourierError("invalid_response", "Modrinth did not return the created project ID.", uncertain=True)
        return self.project(response)

    @api_operation(write=True)
    def upload(self, project, artifact, upload_data):
        self.publication()
        data = {**upload_data, "project_id": project.id}
        response = self.http.multipart("/version", {"data": data}, [("file", artifact.path, artifact.hashes["sha256"])])
        matching = [file for file in self.remote_files([response], project) if same_file(artifact, file)]
        if not matching:
            raise CourierError("verification_failed", "Modrinth response did not contain the expected file hash.", uncertain=True)
        return matching[0]

    def needs_submission(self, project):
        return project is None or project.status == "draft"

    @api_operation(write=True)
    def submit(self, project):
        self.publication()
        current = self.get_project(project.id)
        if not current:
            raise CourierError("project_inaccessible", "Cannot verify the Modrinth project's submission status.")
        if current.status == "draft":
            self.http.json("PATCH", "/project/" + segment(project.id),
                           {"status": "processing", "requested_status": "approved"})
            return "pending_moderation"
        if current.status == "processing":
            return "pending_moderation"
        return current.status

    @api_operation()
    def page_action(self, project):
        publication = self.publication()
        expected = {"title": publication.title, "description": publication.summary, "body": publication.body}
        return "update_page" if any(project.raw.get(key) != value for key, value in expected.items()) else None

    @api_operation(write=True)
    def update_page(self, project):
        publication = self.publication()
        self.http.json("PATCH", "/project/" + segment(project.id), {
            "title": publication.title, "description": publication.summary, "body": publication.body,
        })
        current = self.get_project(project.id)
        if current is None or self.page_action(current):
            raise CourierError("page_unverified", "Modrinth accepted the page update, but its English copy is not yet verified.", uncertain=True)
        return current
