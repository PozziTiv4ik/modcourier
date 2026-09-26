import os
from urllib.parse import urlencode

from .base import Connector, segment
from ..config import repo_url
from ..errors import CourierError
from ..http import Http
from ..models import RemoteFile, RemoteProject

FILE_STATUSES = {
    1: "pending_moderation", 2: "pending_moderation", 3: "pending_moderation",
    4: "processing", 5: "pending_moderation", 6: "published", 7: "rejected",
    8: "rejected", 9: "deleted", 10: "archived", 11: "testing", 12: "draft",
    13: "pending_moderation", 14: "failed", 15: "processing",
}
RELATIONS = {"required": "requiredDependency", "optional": "optionalDependency",
             "incompatible": "incompatible", "embedded": "embeddedLibrary"}
LOADERS = {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "quilt": "Quilt"}


class CurseForge(Connector):
    name = "curseforge"

    def __init__(self, config, api=None, upload_api=None):
        super().__init__(config)
        self.token = os.environ.get(self.settings.get("token_env", "CURSEFORGE_UPLOAD_TOKEN"), "")
        self.key = os.environ.get(self.settings.get("api_key_env", "CURSEFORGE_API_KEY"), "")
        self.api = api or Http("https://api.curseforge.com", {"x-api-key": self.key} if self.key else {})
        self.upload_api = upload_api or Http("https://minecraft.curseforge.com/api",
                                             {"X-Api-Token": self.token} if self.token else {})
        self.metadata = {}

    def require_key(self):
        if not self.key:
            raise CourierError("browser_required",
                               "CurseForge catalog lookup needs CURSEFORGE_API_KEY, separate from the upload token. "
                               "Check the author dashboard with the browser; see the generated handoff.")

    @staticmethod
    def project(data):
        links = data.get("links", {})
        return RemoteProject(
            str(data["id"]), data["slug"], data["name"],
            "published" if data.get("status") == 4 else "pending_moderation",
            links.get("websiteUrl") or "https://www.curseforge.com/minecraft/mc-mods/" + data["slug"],
            links.get("sourceUrl") or "", data,
        )

    def get_project(self, project_id):
        self.require_key()
        response = self.api.get("/v1/mods/" + segment(project_id), missing_ok=True)
        if not response:
            return None
        data = response.get("data")
        if not data:
            return None
        if data.get("gameId") != 432 or data.get("classId") != 6:
            raise CourierError("wrong_project_type", "The bound CurseForge project must be a Minecraft Java mod.")
        return self.project(data)

    def search(self, query):
        self.require_key()
        result, index = [], 0
        while True:
            response = self.api.get("/v1/mods/search?" + urlencode({
                "gameId": 432, "classId": 6, "searchFilter": query, "pageSize": 50, "index": index,
            }))
            batch = response["data"]
            result.extend(self.project(item) for item in batch)
            pagination = response.get("pagination", {})
            count = pagination.get("resultCount", len(batch))
            index += count
            if not count or index >= pagination.get("totalCount", index):
                return result
            if index >= 10000:
                raise CourierError("search_incomplete", "CurseForge search exceeded its result window; bind a verified project ID.")

    def discover(self):
        project_id = self.settings.get("project_id")
        if project_id:
            if self.key:
                project = self.get_project(project_id)
                if project:
                    source = repo_url(self.config.project.get("source_url", ""))
                    if source and project.source_url and repo_url(project.source_url) != source:
                        raise CourierError("source_mismatch", "The bound CurseForge project points to a different source repository.")
                    author = self.settings.get("author")
                    if author and not any(a.get("name", "").casefold() == author.casefold()
                                          for a in project.raw.get("authors", [])):
                        raise CourierError("ownership", "Configured CurseForge author does not match the project's authors.")
                    return project
            if self.settings.get("bootstrap_version"):
                # Explicit bind --new records a browser-verified empty author project.
                slug = self.settings.get("slug") or self.config.project.get("slug", "")
                return RemoteProject(str(project_id), slug, self.config.project.get("title", ""),
                                     "draft", f"https://authors.curseforge.com/#/projects/{project_id}",
                                     raw={"bootstrap": True})
            self.require_key()
            raise CourierError("project_inaccessible", "Bound CurseForge project is not visible in the catalog. Check the author dashboard; do not recreate it.")
        candidates = self.search(self.config.project.get("title") or self.config.project.get("slug", ""))
        source = repo_url(self.config.project.get("source_url", ""))
        author = self.settings.get("author", "")
        slug = self.config.project.get("slug", "")
        title = self.config.project.get("title", "")
        exact = [
            p for p in candidates
            if source and repo_url(p.source_url) == source
            and (p.slug == slug or p.title == title)
            and author and any(a.get("name", "").casefold() == author.casefold()
                               for a in p.raw.get("authors", []))
        ]
        if len(exact) == 1:
            return exact[0]
        if candidates:
            raise CourierError("ambiguous_project", "Found possible CurseForge projects. Verify source/ownership in the author dashboard and bind the correct ID.")
        # Public catalog cannot prove that there is no private/pending project.
        raise CourierError("browser_required", "No public CurseForge match found. Check private/pending projects in the author dashboard before creating one.")

    @staticmethod
    def remote_file(data, project):
        hashes = {}
        for entry in data.get("hashes", []):
            algorithm = {1: "sha1", 2: "md5"}.get(entry["algo"])
            if algorithm:
                hashes[algorithm] = entry["value"]
        versions = data.get("gameVersions", [])
        loaders = [name for name, label in LOADERS.items() if label in versions]
        game_versions = [v for v in versions if v not in LOADERS.values() and v not in {"Client", "Server"}
                         and not v.startswith("Java ")]
        return RemoteFile(
            str(data["id"]), data["displayName"], data["fileName"], hashes,
            FILE_STATUSES.get(data.get("fileStatus"), "unknown"),
            f"{project.url}/files/{data['id']}", loaders, game_versions,
        )

    def files(self, project):
        if project.raw.get("bootstrap"):
            return []
        self.require_key()
        result, index = [], 0
        while True:
            response = self.api.get("/v1/mods/" + segment(project.id) + "/files?" + urlencode({"index": index, "pageSize": 50}))
            batch = response["data"]
            result.extend(self.remote_file(data, project) for data in batch)
            pagination = response.get("pagination", {})
            count = pagination.get("resultCount", len(batch))
            index += count
            if not count or index >= pagination.get("totalCount", index):
                return result
            if index >= 10000:
                raise CourierError("listing_incomplete", "CurseForge's file window was exhausted. Cannot prove a release is absent.")

    def validate(self, artifacts, *, new=False):
        if not self.token:
            raise CourierError("missing_token", "Set CURSEFORGE_UPLOAD_TOKEN (or configured token_env) in the local environment.")
        versions = self.upload_api.get("/game/versions")
        names = {entry["name"].casefold(): entry["name"] for entry in versions}
        for artifact in artifacts:
            bootstrap = self.settings.get("bootstrap_version")
            if bootstrap and artifact.version != bootstrap:
                # A bootstrap assertion is valid only for the originally inspected release.
                if not self.key or not self.get_project(self.settings["project_id"]):
                    raise CourierError("bootstrap_expired", "The first-release binding cannot authorize another version while the project remains invisible.")
            tags = artifact.game_versions + [LOADERS[name] for name in artifact.loaders]
            if artifact.environment in {"client_only", "singleplayer_only"}:
                tags += ["Client"]
            elif artifact.environment in {"server_only", "dedicated_server_only"}:
                tags += ["Server"]
            else:
                tags += ["Client", "Server"]
            if any(tag.casefold() not in names for tag in tags):
                raise CourierError("unsupported_version", f"CurseForge does not recognize release tags: {tags}")
            relations = []
            for target, kind in self.dependency_specs(artifact):
                if target.isdigit():
                    dependency = self.get_project(target)
                else:
                    matches = [p for p in self.search(target) if p.slug == target]
                    dependency = matches[0] if len(matches) == 1 else None
                if not dependency:
                    raise CourierError("missing_dependency", f"Cannot resolve CurseForge dependency {target}.")
                relations.append({"projectID": dependency.id, "slug": dependency.slug, "type": RELATIONS[kind]})
            self.metadata[artifact.key] = {
                "displayName": artifact.display_name, "changelog": self.config.text("release", "changelog"),
                "changelogType": "markdown", "gameVersionNames": [names[tag.casefold()] for tag in tags],
                "releaseType": self.config.release.get("type", "release"),
                "relations": {"projects": relations}, "isMarkedForManualRelease": False,
            }

    def upload(self, project, artifact):
        response = self.upload_api.multipart(
            "/projects/" + segment(project.id) + "/upload-file",
            {"metadata": self.metadata[artifact.key]},
            [("file", artifact.path, artifact.hashes["sha256"])],
        )
        if not isinstance(response, dict) or not isinstance(response.get("id"), int):
            raise CourierError("invalid_response", "CurseForge did not return an uploaded file ID.", uncertain=True)
        return RemoteFile(
            str(response["id"]), artifact.display_name, artifact.path.name, artifact.hashes,
            "accepted_unverified", f"https://www.curseforge.com/minecraft/mc-mods/{project.slug}/files/{response['id']}",
            artifact.loaders, artifact.game_versions,
        )
