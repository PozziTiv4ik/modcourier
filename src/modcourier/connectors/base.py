from abc import ABC, abstractmethod
from functools import wraps
import os
import re
from urllib.parse import quote

from ..config import repo_url
from ..errors import CourierError
from ..models import check_variant, same_file
from ..publication import reviewed_publication


def segment(value):
    return quote(str(value), safe="")


def identifier(value, *, numeric=False):
    """Validate API identities before converting them to the shared string form."""
    if numeric:
        valid = type(value) is int and value > 0
    else:
        valid = isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value)
    if not valid:
        raise CourierError("invalid_response", "The platform returned an invalid project or file ID.")
    return str(value)


def records(value):
    """Empty JSON objects/null are never evidence of an empty account or release list."""
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CourierError("invalid_response", "The platform returned an invalid record list.")
    return value


def api_operation(*, write=False):
    """Keep malformed API payloads inside their platform's failure boundary."""
    def decorate(method):
        @wraps(method)
        def call(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except CourierError as exc:
                if write and exc.code in {"invalid_response", "wrong_project_type", "remote_attention", "listing_incomplete"} and not exc.uncertain:
                    raise CourierError(exc.code, str(exc), uncertain=True) from exc
                raise
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise CourierError("invalid_response", f"{self.name} returned incomplete or malformed API data.",
                                   uncertain=write) from exc
        return call
    return decorate


class Connector(ABC):
    name = ""
    capabilities = {"create_project": False, "upload": True, "discover": True, "page_translations": False}
    credentials = ()

    def __init__(self, config):
        self.config = config
        self.settings = config.settings(self.name)

    def publication(self):
        return reviewed_publication(self.config)

    def check_source(self, project):
        source = repo_url(self.config.project.get("source_url", ""))
        if source and project.source_url and repo_url(project.source_url) != source:
            raise CourierError("source_mismatch", f"The bound {self.name} project points to a different source repository.")

    def validate_project(self, *, new=False):
        """Read-only checks for project creation, page edits or submission."""
        self.publication()

    @classmethod
    def credential_status(cls, config):
        settings = config.settings(cls.name)
        return [
            {"platform": cls.name, "variable": settings.get(setting, default),
             "present": bool(os.environ.get(settings.get(setting, default))),
             "purpose": purpose, "browser_fallback": fallback}
            for setting, default, purpose, fallback in cls.credentials
        ]

    def page_action(self, project):
        """Return update_page when the reviewed copy must be synchronized."""
        return None

    def update_page(self, project):
        raise CourierError("browser_required", f"Update {self.name}'s project page in the author website.")

    def needs_submission(self, project):
        return False

    def files_for(self, project, file_id):
        """A platform ID may identify a version containing several files."""
        return [file for file in self.files(project) if file.id == str(file_id)]

    @api_operation()
    def find_file(self, project, file_id, expected):
        candidates = self.files_for(project, file_id)
        match = next((file for file in candidates if same_file(expected, file)), None)
        if match:
            check_variant(expected, match)
            return match
        if any(set(expected.hashes) & set(file.hashes) for file in candidates):
            raise CourierError("verification_failed", f"Remote file {file_id} does not match the expected hash.",
                               uncertain=True)
        return None

    @abstractmethod
    def discover(self): ...

    @abstractmethod
    def files(self, project): ...

    @abstractmethod
    def prepare_uploads(self, artifacts):
        """Read-only preparation; return {artifact.key: opaque upload data}."""
        ...

    @abstractmethod
    def upload(self, project, artifact, upload_data): ...

    @abstractmethod
    def get_project(self, project_id): ...

    def create_project(self, artifacts):
        raise CourierError("browser_required", f"{self.name} requires its author website to create a project.")

    def submit(self, project):
        return project.status

    def dependency_specs(self, artifact):
        mappings = self.config.raw.get("dependencies", {})
        result = []
        for dependency in artifact.dependencies:
            entry = mappings.get(dependency.mod_id, {})
            if not isinstance(entry, dict):
                raise CourierError("dependency_mapping", f"dependencies.{dependency.mod_id} must be an object.")
            if isinstance(entry.get("ignore"), str) and entry["ignore"].strip():
                continue
            target = entry.get(self.name)
            if not target:
                raise CourierError(
                    "dependency_mapping",
                    f"Map dependencies.{dependency.mod_id}.{self.name} to its project ID or slug; "
                    "use an explicit ignore reason only for embedded/internal dependencies."
                )
            kind = entry.get("type", dependency.kind)
            if kind not in {"required", "optional", "incompatible", "embedded"}:
                raise CourierError("dependency_mapping", f"Unknown dependency type for {dependency.mod_id}.")
            result.append((str(target), kind))
        return result
