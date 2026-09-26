import os
import re

from .base import Connector, segment
from ..config import local_path, repo_url
from ..errors import CourierError
from ..http import Http
from ..models import RemoteFile, RemoteProject, file_hashes


class Modrinth(Connector):
    name = "modrinth"
    capabilities = {"create_project": True, "upload": True, "discover": True}

    def __init__(self, config, http=None):
        super().__init__(config)
        self.token = os.environ.get(self.settings.get("token_env", "MODRINTH_TOKEN"), "")
        self.http = http or Http("https://api.modrinth.com/v2", {"Authorization": self.token} if self.token else {})
        self.dependencies = {}
        self.user = None

    def authenticated(self):
        if not self.token:
            raise CourierError("missing_token", "Set MODRINTH_TOKEN (or configured token_env) in the local environment.")
        if self.user is None:
            self.user = self.http.get("/user")
        return self.user

    @staticmethod
    def project(data):
        return RemoteProject(
            str(data["id"]), data["slug"], data["title"], data["status"],
            "https://modrinth.com/mod/" + data["slug"], data.get("source_url") or "", data,
        )

    def get_project(self, project_id):
        data = self.http.get("/project/" + segment(project_id), missing_ok=True)
        return self.project(data) if data else None

    def discover(self):
        user = self.authenticated()
        project_id = self.settings.get("project_id")
        owned = self.http.get("/user/" + segment(user["id"]) + "/projects")
        if not isinstance(owned, list):
            raise CourierError("invalid_response", "Modrinth returned an invalid project list.")
        if project_id:
            project = self.get_project(project_id)
            if project is None:
                raise CourierError("project_inaccessible", "Bound Modrinth project is missing or inaccessible; do not recreate it.")
            source = repo_url(self.config.project.get("source_url", ""))
            if source and project.source_url and repo_url(project.source_url) != source:
                raise CourierError("source_mismatch", "The bound Modrinth project points to a different source repository.")
            # Explicit binding supports organization/team projects too; the API enforces write scopes.
            if not any(str(p["id"]) == project.id for p in owned):
                members = self.http.get("/project/" + segment(project.id) + "/members")
                if not any(str(m.get("user", {}).get("id")) == str(user["id"]) and m.get("accepted", False) for m in members):
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

    def files(self, project):
        versions = self.http.get("/project/" + segment(project.id) + "/version")
        result = []
        for version in versions:
            for file in version.get("files", []):
                status = version.get("status", "unknown")
                if project.status == "draft" and status in {"listed", "unlisted"}:
                    status = "uploaded"
                elif project.status not in {"approved", "unlisted"}:
                    status = "pending_moderation" if project.status == "processing" else project.status
                elif status in {"listed", "unlisted"}:
                    status = "published"
                result.append(RemoteFile(
                    str(version["id"]), version["version_number"], file["filename"], file["hashes"],
                    status, f"{project.url}/version/{version['id']}",
                    version.get("loaders", []), version.get("game_versions", []),
                ))
        return result

    def validate(self, artifacts, *, new=False):
        self.authenticated()
        policy = self.config.raw.get("policy", {}).get("ai_usage", "unknown")
        if policy == "primary":
            raise CourierError("policy_blocked", "Modrinth prohibits public projects primarily generated by AI. See docs/setup.md.")
        if policy not in {"none", "assisted", "substantial"}:
            raise CourierError("policy_input", "Set policy.ai_usage truthfully: none, assisted, substantial or primary.")
        if policy == "substantial" and not self.settings.get("disclosures_confirmed"):
            raise CourierError("browser_required", "Apply the required Modrinth AI disclosures in the project settings, then confirm them with bind --disclosures-confirmed.")
        known_versions = {v["version"] for v in self.http.get("/tag/game_version")}
        known_loaders = {v["name"] for v in self.http.get("/tag/loader")}
        for artifact in artifacts:
            if set(artifact.game_versions) - known_versions:
                raise CourierError("unsupported_version", f"Modrinth does not recognize {artifact.game_versions}.")
            if set(artifact.loaders) - known_loaders:
                raise CourierError("unsupported_loader", f"Modrinth does not recognize {artifact.loaders}.")
            dependencies = []
            for target, kind in self.dependency_specs(artifact):
                project = self.get_project(target)
                if not project:
                    raise CourierError("missing_dependency", f"Modrinth dependency {target} is missing or inaccessible.")
                dependencies.append({"project_id": project.id, "dependency_type": kind})
            self.dependencies[artifact.key] = dependencies
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
            licenses = {entry["short"] for entry in self.http.get("/tag/license")} | {"ARR", "LicenseRef-Custom"}
            if values["license"] not in licenses:
                raise CourierError("project_metadata", "Choose a valid SPDX license, ARR, or LicenseRef-Custom with license_url.")
            if values["license"] == "LicenseRef-Custom" and not values.get("license_url"):
                raise CourierError("project_metadata", "A custom license requires project.license_url.")
            categories = self.settings.get("categories", [])
            known = {entry["name"] for entry in self.http.get("/tag/category") if entry.get("project_type") == "mod"}
            if not categories or not set(categories).issubset(known) or len(categories) > 3:
                raise CourierError("project_metadata", "Choose 1-3 valid modrinth.categories from /tag/category.")
            if values.get("icon"):
                icon = local_path(self.config.root, values["icon"])
                if not icon.is_file():
                    raise CourierError("project_metadata", "project.icon file is missing.")

    def create_project(self, artifacts):
        values = self.config.project
        data = {
            "slug": values["slug"], "title": values["title"], "description": values["summary"],
            "body": self.config.text("project", "body"), "categories": self.settings["categories"],
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

    def upload(self, project, artifact):
        data = {
            "project_id": project.id, "name": artifact.display_name, "version_number": artifact.key,
            "changelog": self.config.text("release", "changelog"),
            "dependencies": self.dependencies[artifact.key], "game_versions": artifact.game_versions,
            "version_type": self.config.release.get("type", "release"), "loaders": artifact.loaders,
            "featured": True, "file_parts": ["file"], "primary_file": "file",
            "environment": artifact.environment,
        }
        response = self.http.multipart("/version", {"data": data}, [("file", artifact.path, artifact.hashes["sha256"])])
        if not isinstance(response, dict) or not response.get("id"):
            raise CourierError("invalid_response", "Modrinth did not return a version ID.", uncertain=True)
        matching = [f for f in response.get("files", []) if f.get("hashes", {}).get("sha512") == artifact.hashes["sha512"]]
        if not matching:
            raise CourierError("verification_failed", "Modrinth response did not contain the expected file hash.", uncertain=True)
        return RemoteFile(
            str(response["id"]), response["version_number"], artifact.path.name, matching[0]["hashes"],
            "uploaded", f"{project.url}/version/{response['id']}", artifact.loaders, artifact.game_versions,
        )

    def submit(self, project):
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
