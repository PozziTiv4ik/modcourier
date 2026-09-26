from dataclasses import asdict, dataclass, field
from pathlib import Path
import hashlib


@dataclass
class Dependency:
    mod_id: str
    kind: str = "required"


@dataclass
class Artifact:
    path: Path
    mod_id: str
    title: str
    version: str
    loaders: list[str]
    game_versions: list[str]
    description: str = ""
    license: str = ""
    environment: str = ""
    dependencies: list[Dependency] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    release_id: str = ""

    @property
    def key(self):
        return self.release_id or (
            self.version + "+" + ".".join(sorted(self.loaders))
            + ".mc" + "-".join(sorted(self.game_versions))
        )

    @property
    def display_name(self):
        return f"{self.title} {self.key}"

    def as_dict(self):
        data = asdict(self)
        data["path"] = str(self.path)
        data["release_id"] = self.key
        return data


@dataclass
class RemoteFile:
    id: str
    label: str
    filename: str
    hashes: dict[str, str]
    status: str
    url: str
    loaders: list[str] = field(default_factory=list)
    game_versions: list[str] = field(default_factory=list)


@dataclass
class RemoteProject:
    id: str
    slug: str
    title: str
    status: str
    url: str
    source_url: str = ""
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Step:
    platform: str
    action: str
    reason: str
    project_id: str = ""
    artifact: Artifact | None = None
    remote: RemoteFile | None = None
    details: dict = field(default_factory=dict)

    @property
    def key(self):
        suffix = self.artifact.key if self.artifact else self.action
        return f"{self.platform}:{suffix}"

    def as_dict(self):
        data = {
            "key": self.key, "platform": self.platform, "action": self.action,
            "reason": self.reason, "project_id": self.project_id, "details": self.details,
        }
        if self.artifact:
            data["artifact"] = self.artifact.as_dict()
        if self.remote:
            data["remote"] = asdict(self.remote)
        return data


def file_hashes(path: Path):
    digests = {name: hashlib.new(name) for name in ("sha256", "sha512", "sha1", "md5")}
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            for digest in digests.values():
                digest.update(chunk)
    return {name: digest.hexdigest() for name, digest in digests.items()}


def same_file(artifact: Artifact, remote: RemoteFile):
    for name in ("sha512", "sha256", "sha1", "md5"):
        if name in remote.hashes and name in artifact.hashes:
            return remote.hashes[name].lower() == artifact.hashes[name].lower()
    return False
