import os
from urllib.parse import urlencode

from .base import Connector, api_operation, identifier, records, segment
from ..config import repo_url
from ..errors import CourierError
from ..http import Http
from ..models import RemoteFile, RemoteProject
from ..publication import check_copy, digest

# Official catalog enums: https://docs.curseforge.com/rest-api/#filestatus
FILE_STATUSES = {
    1: "processing", 2: "changes_required", 3: "pending_moderation",
    4: "approved", 5: "rejected", 6: "malware_detected", 7: "deleted",
    8: "archived", 9: "testing", 10: "released", 11: "pending_moderation",
    12: "deprecated", 13: "processing", 14: "processing", 15: "failed",
    16: "processing", 17: "processing", 18: "pending_moderation",
    19: "processing", 20: "processing", 21: "processing",
    22: "processing", 23: "processing",
}
PROJECT_STATUSES = {
    1: "draft", 2: "changes_required", 3: "pending_moderation", 4: "published",
    5: "rejected", 6: "pending_moderation", 7: "inactive", 8: "abandoned",
    9: "deleted", 10: "pending_moderation",
}
RELATIONS = {"required": "requiredDependency", "optional": "optionalDependency",
             "incompatible": "incompatible", "embedded": "embeddedLibrary"}
LOADERS = {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "quilt": "Quilt"}


class CurseForge(Connector):
    name = "curseforge"
    credentials = (
        ("token_env", "CURSEFORGE_UPLOAD_TOKEN", "CurseForge uploads", False),
        ("api_key_env", "CURSEFORGE_API_KEY", "CurseForge catalog discovery and verification", True),
    )

    def __init__(self, config, api=None, upload_api=None):
        super().__init__(config)
        self.token = os.environ.get(self.settings.get("token_env", "CURSEFORGE_UPLOAD_TOKEN"), "")
        self.key = os.environ.get(self.settings.get("api_key_env", "CURSEFORGE_API_KEY"), "")
        self.api = api or Http("https://api.curseforge.com", {"x-api-key": self.key} if self.key else {})
        self.upload_api = upload_api or Http("https://minecraft.curseforge.com/api",
                                             {"X-Api-Token": self.token} if self.token else {})

    def require_key(self):
        if not self.key:
            raise CourierError("catalog_unavailable",
                               "CurseForge catalog lookup needs CURSEFORGE_API_KEY, separate from the upload token. "
                               "Check the author dashboard with the browser; see the generated handoff.")

    @staticmethod
    def project(data):
        if data.get("gameId") != 432 or data.get("classId") != 6:
            raise CourierError("wrong_project_type", "The CurseForge project must be a Minecraft Java mod.")
        links = data.get("links", {})
        return RemoteProject(
            identifier(data["id"], numeric=True), data["slug"], data["name"],
            PROJECT_STATUSES.get(data.get("status"), "unknown"),
            links.get("websiteUrl") or "https://www.curseforge.com/minecraft/mc-mods/" + data["slug"],
            links.get("sourceUrl") or "", data,
        )

    @api_operation()
    def get_project(self, project_id):
        self.require_key()
        response = self.api.get("/v1/mods/" + segment(project_id), missing_ok=True)
        if response is None:
            return None
        data = response["data"]
        if str(data["id"]) != str(project_id):
            raise CourierError("invalid_response", "CurseForge returned a different project ID.")
        return self.project(data)

    def pages(self, path, params, *, code):
        """Read a complete catalog listing, or fail without asserting absence."""
        self.require_key()
        result, index = [], 0
        expected_total, seen = None, set()
        while True:
            page_size = min(50, 10000 - index)
            response = self.api.get(path + "?" + urlencode({**params, "pageSize": page_size, "index": index}))
            batch = response["data"]
            pagination = response.get("pagination")
            if not isinstance(batch, list) or not isinstance(pagination, dict):
                raise CourierError(code, "CurseForge omitted listing data or pagination; cannot prove a release is absent.")
            count, total, offset = (pagination.get(key) for key in ("resultCount", "totalCount", "index"))
            if (any(type(value) is not int for value in (count, total, offset))
                or count != len(batch) or count > page_size or offset != index
                or total < index + count or (count == 0 and index < total)):
                raise CourierError(code, "CurseForge returned inconsistent pagination; inspect the author dashboard.")
            if expected_total is not None and total != expected_total:
                raise CourierError(code, "CurseForge's listing changed during pagination; inspect again before uploading.")
            expected_total = total
            for entry in records(batch):
                entry_id = identifier(entry["id"], numeric=True)
                if entry_id in seen:
                    raise CourierError(code, "CurseForge repeated a result during pagination; cannot prove a release is absent.")
                seen.add(entry_id)
            result.extend(batch)
            index += count
            if index >= total:
                return result
            if index >= 10000:
                raise CourierError(code, "CurseForge's result window was exhausted; cannot prove a release is absent.")

    @api_operation()
    def search(self, query):
        return [self.project(data) for data in self.pages("/v1/mods/search", {
            "gameId": 432, "classId": 6, "searchFilter": query,
        }, code="search_incomplete")]

    @api_operation()
    def discover(self):
        project_id = self.settings.get("project_id")
        if project_id:
            if self.key:
                project = self.get_project(project_id)
                if project:
                    self.check_source(project)
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
        if identifier(data["modId"], numeric=True) != project.id:
            raise CourierError("verification_failed", "CurseForge returned a file from another project.", uncertain=True)
        hashes = {}
        for entry in records(data.get("hashes", [])):
            algorithm = {1: "sha1", 2: "md5"}.get(entry["algo"])
            if algorithm:
                hashes[algorithm] = entry["value"]
        versions = data.get("gameVersions", [])
        if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
            raise CourierError("invalid_response", "CurseForge returned invalid compatibility tags.")
        loaders = [name for name, label in LOADERS.items() if label in versions]
        game_versions = [v for v in versions if v not in LOADERS.values() and v not in {"Client", "Server"}
                         and not v.startswith("Java ")]
        status = FILE_STATUSES.get(data.get("fileStatus"), "unknown")
        if status in {"approved", "released"}:
            if data.get("isAvailable") is not True:
                status = "unavailable"
            elif project.status != "published":
                status = project.status
            elif data.get("isEarlyAccessContent"):
                status = "early_access"
            else:
                status = "published"
        return RemoteFile(
            identifier(data["id"], numeric=True), data["displayName"], data["fileName"], hashes,
            status,
            f"{project.url}/files/{data['id']}", loaders, game_versions,
        )

    @api_operation()
    def files(self, project):
        if project.raw.get("bootstrap"):
            return []
        return [self.remote_file(data, project) for data in self.pages(
            "/v1/mods/" + segment(project.id) + "/files", {}, code="listing_incomplete")]

    @api_operation()
    def files_for(self, project, file_id):
        self.require_key()
        response = self.api.get("/v1/mods/" + segment(project.id) + "/files/" + segment(file_id), missing_ok=True)
        if response is None:
            return []
        file = self.remote_file(response["data"], project)
        if file.id != str(file_id):
            raise CourierError("verification_failed", "CurseForge returned a different file ID.", uncertain=True)
        return [file]

    @api_operation()
    def validate_project(self, *, new=False):
        self.publication()
        if not self.token:
            raise CourierError("missing_token", "Set CURSEFORGE_UPLOAD_TOKEN (or configured token_env) in the local environment.")

    @api_operation()
    def prepare_uploads(self, artifacts):
        publication = self.publication()
        versions = records(self.upload_api.get("/game/versions"))
        names = {entry["name"].casefold(): entry["name"] for entry in versions}
        prepared, resolved = {}, {}
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
                if target not in resolved:
                    if target.isascii() and target.isdigit():
                        resolved[target] = self.get_project(target)
                    else:
                        matches = [p for p in self.search(target) if p.slug == target]
                        resolved[target] = matches[0] if len(matches) == 1 else None
                dependency = resolved[target]
                if not dependency:
                    raise CourierError("missing_dependency", f"Cannot resolve CurseForge dependency {target}.")
                relations.append({"projectID": dependency.id, "slug": dependency.slug, "type": RELATIONS[kind]})
            prepared[artifact.key] = {
                "displayName": publication.release_name(artifact), "changelog": publication.changelog,
                "changelogType": "markdown", "gameVersionNames": [names[tag.casefold()] for tag in tags],
                "releaseType": self.config.release.get("type", "release"),
                "relations": {"projects": relations}, "isMarkedForManualRelease": False,
            }
        return prepared

    @api_operation(write=True)
    def upload(self, project, artifact, upload_data):
        publication = self.publication()
        response = self.upload_api.multipart(
            "/projects/" + segment(project.id) + "/upload-file",
            {"metadata": upload_data},
            [("file", artifact.path, artifact.hashes["sha256"])],
        )
        if not isinstance(response, dict) or type(response.get("id")) is not int or response["id"] <= 0:
            raise CourierError("invalid_response", "CurseForge did not return an uploaded file ID.", uncertain=True)
        return RemoteFile(
            str(response["id"]), publication.release_name(artifact), artifact.path.name, artifact.hashes,
            "accepted_unverified", f"https://www.curseforge.com/minecraft/mc-mods/{project.slug}/files/{response['id']}",
            artifact.loaders, artifact.game_versions,
        )

    @api_operation()
    def page_copy(self, project):
        self.require_key()
        response = self.api.get("/v1/mods/" + segment(project.id) + "/description")
        body = response.get("data")
        if not isinstance(body, str):
            raise CourierError("invalid_response", "CurseForge did not return its project description.")
        return {"title": project.title, "summary": project.raw.get("summary", ""), "body": body}

    @api_operation()
    def confirm_page(self, project_id):
        """Record the caller's browser review; HTML may differ from source Markdown."""
        publication = self.publication()
        project = self.get_project(project_id) if self.key else None
        remote_digest = ""
        if project:
            fields = self.page_copy(project)
            check_copy(fields)
            if fields["title"] != publication.title or fields["summary"] != publication.summary:
                raise CourierError("page_unverified", "CurseForge title/summary still differ from the reviewed English copy. Save the page and wait for its API view to update.")
            remote_digest = digest(fields)
        return {"sha256": publication.page_sha256, "remote_sha256": remote_digest}

    @api_operation()
    def page_action(self, project):
        publication = self.publication()
        review = self.settings.get("page_review", {})
        if project.raw.get("bootstrap"):
            if review.get("sha256") == publication.page_sha256:
                return None
        else:
            fields = self.page_copy(project)
            if fields == publication.page:
                return None
            if (review.get("sha256") == publication.page_sha256
                and review.get("remote_sha256") == digest(fields)):
                return None
        raise CourierError(
            "page_review_required",
            "Set the CurseForge page title, summary and description to the reviewed English copy. "
            "Check the rendered page, then run bind curseforge PROJECT_ID --page-confirmed. "
            "The author Upload API cannot edit the project page."
        )
