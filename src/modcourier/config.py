from pathlib import Path
import re
from urllib.parse import urlsplit

from .errors import CourierError
from .state import atomic_json, read_json

PLATFORMS = ("modrinth", "curseforge")
ENVIRONMENTS = {
    "client_and_server", "client_only", "client_only_server_optional", "singleplayer_only",
    "server_only", "server_only_client_optional", "dedicated_server_only",
    "client_or_server", "client_or_server_prefers_both",
}


def local_path(root: Path, value: str):
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise CourierError("outside_project", f"Path must stay within the mod project: {value}")
    return path


def repo_url(value):
    value = value.strip()
    if value.startswith("git@"):
        value = "https://" + value[4:].replace(":", "/", 1)
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return ""
    return "https://" + parsed.hostname.lower() + parsed.path.rstrip("/").removesuffix(".git")


class Config:
    def __init__(self, root: Path, raw=None):
        self.root = root.resolve()
        self.path = self.root / "modcourier.json"
        self.raw = read_json(self.path) if raw is None else raw
        if (not isinstance(self.raw, dict) or type(self.raw.get("schema_version", 1)) is not int
            or self.raw.get("schema_version", 1) != 1):
            raise CourierError("config_version", "modcourier.json must be a schema_version: 1 object.")
        allowed = {"schema_version", "project", "release", "artifacts", "platforms", "dependencies", "policy", "publication"}
        unknown = set(self.raw) - allowed
        if unknown:
            raise CourierError("unknown_config", f"Unknown configuration fields: {', '.join(sorted(unknown))}")
        for field in ("project", "release", "platforms", "dependencies", "policy", "publication"):
            if not isinstance(self.raw.get(field, {}), dict):
                raise CourierError("invalid_config", f"{field} must be an object.")
        fields = {
            "project": {"mod_id", "slug", "title", "summary", "license", "license_url", "source_url",
                        "issues_url", "wiki_url", "discord_url", "body", "body_file", "icon"},
            "release": {"type", "game_versions", "environment", "changelog", "changelog_file"},
            "policy": {"ai_usage"},
            "publication": {"language", "reviewed_sha256"},
        }
        for section, names in fields.items():
            if set(self.raw.get(section, {})) - names:
                raise CourierError("unknown_config", f"Unknown fields in {section}: {sorted(set(self.raw[section]) - names)}")
        if any(not isinstance(v, str) for v in self.project.values()):
            raise CourierError("invalid_config", "All project fields must be strings.")
        publication = self.raw.get("publication", {})
        if publication.get("language", "en") != "en":
            raise CourierError("publication_language", "Website publications must use English (publication.language: en).")
        if "reviewed_sha256" in publication and (
            not isinstance(publication["reviewed_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}|", publication["reviewed_sha256"])
        ):
            raise CourierError("invalid_config", "publication.reviewed_sha256 must be a SHA-256 hash written by review-language.")
        for field in ("source_url", "issues_url", "wiki_url", "discord_url", "license_url"):
            value = self.project.get(field)
            if value:
                parsed = urlsplit(value)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    raise CourierError("invalid_config", f"project.{field} must be an HTTPS URL without embedded credentials.")
        if not isinstance(self.raw.get("policy", {}).get("ai_usage", "unknown"), str):
            raise CourierError("invalid_config", "policy.ai_usage must be a string.")
        if "game_versions" in self.release and (
            not isinstance(self.release["game_versions"], list)
            or not all(isinstance(v, str) for v in self.release["game_versions"])
        ):
            raise CourierError("invalid_config", "release.game_versions must be a list of strings.")
        for key, value in self.release.items():
            if key != "game_versions" and not isinstance(value, str):
                raise CourierError("invalid_config", f"release.{key} must be a string.")
        for name, settings in self.platforms.items():
            if name not in PLATFORMS or not isinstance(settings, dict):
                raise CourierError("invalid_platform", f"Unsupported platform configuration: {name}")
            allowed_settings = {"enabled", "project_id", "slug", "author", "token_env", "api_key_env",
                                "categories", "bootstrap_version", "disclosures_confirmed", "page_review"}
            if set(settings) - allowed_settings:
                raise CourierError("unknown_config", f"Unknown {name} settings: {sorted(set(settings) - allowed_settings)}")
            if "page_review" in settings:
                review = settings["page_review"]
                if (name != "curseforge" or not isinstance(review, dict)
                    or set(review) != {"sha256", "remote_sha256"}
                    or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}|", v) for v in review.values())):
                    raise CourierError("invalid_config", "CurseForge page_review must be recorded by bind --page-confirmed.")
            for key in ("enabled", "disclosures_confirmed"):
                if key in settings and not isinstance(settings[key], bool):
                    raise CourierError("invalid_config", f"{name}.{key} must be true or false.")
            if "categories" in settings and (not isinstance(settings["categories"], list)
                or not all(isinstance(v, str) for v in settings["categories"])):
                raise CourierError("invalid_config", f"{name}.categories must be a list of strings.")
            for key in ("slug", "author", "token_env", "api_key_env", "bootstrap_version"):
                if key in settings and not isinstance(settings[key], str):
                    raise CourierError("invalid_config", f"{name}.{key} must be a string.")
            for key in ("token_env", "api_key_env"):
                if key in settings and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", settings[key]):
                    raise CourierError("invalid_config", f"{name}.{key} must be an environment variable NAME.")
            project_id = settings.get("project_id", "")
            if not isinstance(project_id, str) and type(project_id) is not int:
                raise CourierError("invalid_config", f"{name}.project_id must be a string or integer ID.")
            if name == "curseforge" and project_id != "" and not re.fullmatch(r"[1-9][0-9]*", str(project_id)):
                raise CourierError("invalid_config", "CurseForge project_id must be a numeric ID.")
            if name == "modrinth" and settings.get("project_id") and not re.fullmatch(r"[A-Za-z0-9_-]+", str(settings["project_id"])):
                raise CourierError("invalid_config", "Invalid Modrinth project ID or slug.")

    @property
    def project(self):
        return self.raw.get("project", {})

    @property
    def release(self):
        return self.raw.get("release", {})

    @property
    def platforms(self):
        return self.raw.get("platforms", {name: {"enabled": True} for name in PLATFORMS})

    def settings(self, platform):
        return self.platforms.get(platform, {})

    def enabled(self, selection=None):
        names = list(dict.fromkeys(selection or PLATFORMS))
        if any(name not in PLATFORMS for name in names):
            raise CourierError("invalid_platform", "Choose a registered publication platform.")
        enabled = [name for name in names if self.settings(name).get("enabled", True)]
        if not enabled:
            raise CourierError("no_platforms", "No selected platform is enabled. Enable a target in modcourier.json.")
        return enabled

    def text(self, section, field):
        settings = self.raw.get(section, {})
        if settings.get(field + "_file"):
            path = local_path(self.root, settings[field + "_file"])
            if not path.is_file():
                raise CourierError("missing_file", f"Missing {section}.{field}_file: {path.name}")
            return path.read_text(encoding="utf-8-sig")
        return str(settings.get(field, ""))

    def bind(self, platform, project_id, **extra):
        import copy
        candidate = copy.deepcopy(self.raw)
        candidate.setdefault("platforms", {}).setdefault(platform, {}).update(
            project_id=str(project_id), **extra
        )
        Config(self.root, candidate)
        self.raw = candidate
        atomic_json(self.path, self.raw)
