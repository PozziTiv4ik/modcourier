from abc import ABC, abstractmethod
from urllib.parse import quote

from ..errors import CourierError


def segment(value):
    return quote(str(value), safe="")


class Connector(ABC):
    name = ""
    capabilities = {"create_project": False, "upload": True, "discover": True}

    def __init__(self, config):
        self.config = config
        self.settings = config.settings(self.name)

    @abstractmethod
    def discover(self): ...

    @abstractmethod
    def files(self, project): ...

    @abstractmethod
    def validate(self, artifacts, *, new=False): ...

    @abstractmethod
    def upload(self, project, artifact): ...

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
