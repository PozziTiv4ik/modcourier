from pathlib import Path
import re
import subprocess

from .config import Config, ENVIRONMENTS, local_path, repo_url
from .errors import CourierError
from .metadata import read_artifact

EXCLUDED = re.compile(r"-(?:sources|javadoc|dev|dev-shadow|slim|test|tests)\.jar$", re.I)


def artifacts(config: Config):
    entries = config.raw.get("artifacts")
    if entries is None:
        paths = sorted(set(config.root.glob("build/libs/*.jar")) | set(config.root.glob("*/build/libs/*.jar")))
        entries = [{"path": str(p.relative_to(config.root))} for p in paths if not EXCLUDED.search(p.name)]
    if not isinstance(entries, list):
        raise CourierError("invalid_config", "artifacts must be a list of paths or objects with path.")
    result, seen = [], set()
    for entry in entries:
        entry = {"path": entry} if isinstance(entry, str) else entry
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise CourierError("invalid_config", "Each artifact needs a relative path.")
        if set(entry) - {"path", "game_versions", "environment", "release_id"}:
            raise CourierError("unknown_config", "Artifact settings support path, game_versions, environment and release_id.")
        for key in ("environment", "release_id"):
            if key in entry and not isinstance(entry[key], str):
                raise CourierError("invalid_config", f"Artifact {key} must be a string.")
        path = local_path(config.root, entry["path"])
        if not path.is_file() or path.suffix.lower() != ".jar":
            raise CourierError("missing_artifact", f"Build the release JAR first: {entry['path']}")
        if EXCLUDED.search(path.name):
            raise CourierError("non_release_jar", f"Refusing development/source artifact: {path.name}")
        if path.stat().st_size > 512 * 1024 * 1024:
            raise CourierError("artifact_size", f"{path.name} exceeds the 512 MiB upload limit.")
        overrides = {k: config.release[k] for k in ("game_versions", "environment") if k in config.release}
        overrides.update({k: v for k, v in entry.items() if k != "path"})
        artifact = read_artifact(path, overrides)
        signature = (artifact.hashes["sha256"], tuple(artifact.game_versions), tuple(artifact.loaders))
        if signature not in seen:
            result.append(artifact)
            seen.add(signature)
    if not result:
        raise CourierError("no_artifacts", "No release JAR found. Run this mod's Gradle build, then inspect again.")
    if len({a.mod_id for a in result}) != 1:
        raise CourierError("multiple_mods", "Multiple mod IDs found. Select one mod's artifacts in modcourier.json.")
    if len({a.version for a in result}) != 1:
        raise CourierError("multiple_versions", "Old and new JARs are mixed. Select only the intended release artifacts.")
    if len({a.key for a in result}) != len(result):
        raise CourierError("duplicate_variant", "Two different artifacts have the same release ID. Select the right JAR.")
    if config.project.get("mod_id") and config.project["mod_id"] != result[0].mod_id:
        raise CourierError("wrong_mod", "Configured mod_id does not match the built JAR.")
    for artifact in result:
        if not isinstance(artifact.game_versions, list) or not all(isinstance(v, str) for v in artifact.game_versions):
            raise CourierError("invalid_config", "game_versions must be a list of tested Minecraft version strings.")
        if artifact.environment and artifact.environment not in ENVIRONMENTS:
            raise CourierError("invalid_environment", f"Unknown environment: {artifact.environment}")
    return result


def git_source(root: Path):
    try:
        output = subprocess.run(
            ["git", "remote", "get-url", "origin"], cwd=root, capture_output=True,
            text=True, timeout=10, check=False,
        )
        return repo_url(output.stdout) if output.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def suggested_config(root, items):
    root = root.resolve()
    first = items[0]
    deps = sorted({d.mod_id for a in items for d in a.dependencies})
    body_file = next((name for name in ("README.en.md", "README.md") if (root / name).is_file()), None)
    changelog_file = next((name for name in ("CHANGELOG.en.md", "CHANGELOG.md") if (root / name).is_file()), None)
    return {
        "schema_version": 1,
        "project": {
            "mod_id": first.mod_id, "slug": first.mod_id.replace("_", "-"), "title": first.title,
            "summary": first.description[:255], "license": first.license,
            "source_url": git_source(root),
            **({"body_file": body_file} if body_file else {"body": ""}),
        },
        "release": {
            "type": "release", "game_versions": first.game_versions,
            "environment": first.environment,
            **({"changelog_file": changelog_file} if changelog_file else {"changelog": ""}),
        },
        "artifacts": [{"path": str(a.path.relative_to(root)).replace("\\", "/"),
                       **({"game_versions": a.game_versions} if a.game_versions != first.game_versions else {}),
                       **({"environment": a.environment} if a.environment and a.environment != first.environment else {})}
                      for a in items],
        "platforms": {
            "modrinth": {"enabled": True, "categories": [], "token_env": "MODRINTH_TOKEN"},
            "curseforge": {"enabled": True, "token_env": "CURSEFORGE_UPLOAD_TOKEN",
                          "api_key_env": "CURSEFORGE_API_KEY"},
        },
        "dependencies": {d: {"modrinth": "", "curseforge": ""} for d in deps},
        "policy": {"ai_usage": "unknown"},
        "publication": {"language": "en", "reviewed_sha256": ""},
    }


def local_issues(config, items):
    issues = []
    for artifact in items:
        if not artifact.game_versions:
            issues.append(f"{artifact.path.name}: specify tested game_versions; ranges are not guessed.")
        if not artifact.environment:
            issues.append(f"{artifact.path.name}: specify release.environment (client/server behavior).")
    if config.release.get("type", "release") not in {"release", "beta", "alpha"}:
        issues.append("release.type must be release, beta or alpha.")
    if not config.text("release", "changelog").strip():
        issues.append("Provide release.changelog or release.changelog_file for this release.")
    return issues
