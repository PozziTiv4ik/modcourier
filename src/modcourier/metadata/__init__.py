"""Read built artifacts, never execute build scripts to discover metadata."""
import json
from pathlib import Path
import re
import tomllib
import zipfile

from ..errors import CourierError
from ..models import Artifact, Dependency, RESERVED_RELEASE_IDS, file_hashes
from . import fabric, forge, neoforge, quilt

BUILTINS = {"minecraft", "java", "fabricloader", "forge", "neoforge", "quilt_loader"}
MAX_METADATA = 2 * 1024 * 1024


def exact_versions(value):
    """Infer only exact releases. Ranges need a tested-version list."""
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if not isinstance(item, str):
            return []
        item = item.strip()
        if item.startswith("[") and item.endswith("]") and "," not in item:
            item = item[1:-1]
        item = item.removeprefix("=")
        if not re.fullmatch(r"(?:\d+\.)+\d+(?:-(?:pre|rc)\d+)?|\d{2}w\d{2}[a-z]", item):
            return []
        result.append(item)
    return sorted(set(result))


def read_artifact(path: Path, overrides=None):
    overrides = overrides or {}
    try:
        with zipfile.ZipFile(path) as archive:
            def read(name):
                entry = archive.getinfo(name)
                if entry.file_size > MAX_METADATA:
                    raise CourierError("metadata_size", f"{name} exceeds the metadata size limit.")
                return archive.read(name).decode("utf-8-sig")

            names = set(archive.namelist())
            parsed = []
            if "fabric.mod.json" in names:
                parsed.append(fabric.parse(json.loads(read("fabric.mod.json"))))
            if "quilt.mod.json" in names:
                parsed.append(quilt.parse(json.loads(read("quilt.mod.json"))))
            manifest = {}
            if "META-INF/MANIFEST.MF" in names:
                unfolded = read("META-INF/MANIFEST.MF").replace("\r\n ", "").replace("\n ", "")
                manifest = dict(line.split(": ", 1) for line in unfolded.splitlines() if ": " in line)
            if "META-INF/neoforge.mods.toml" in names:
                parsed.append(neoforge.parse(tomllib.loads(read("META-INF/neoforge.mods.toml")), manifest))
            elif "META-INF/mods.toml" in names:
                parsed.append(forge.parse(tomllib.loads(read("META-INF/mods.toml")), manifest))
            if not parsed:
                raise CourierError("not_a_mod", f"{path.name} has no supported mod descriptor.")
    except (OSError, zipfile.BadZipFile, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CourierError("invalid_artifact", f"Cannot read mod metadata in {path.name}: {exc}") from exc
    for descriptor in parsed:
        if any(not isinstance(descriptor.get(key), str) for key in
               ("mod_id", "version", "title", "description", "license", "environment")):
            raise CourierError("invalid_artifact", f"{path.name}: metadata text fields must be strings.")
    if len({p["mod_id"] for p in parsed}) != 1 or len({p["version"] for p in parsed}) != 1:
        raise CourierError("mixed_metadata", f"{path.name}: loader descriptors disagree about ID or version.")
    data = dict(parsed[0])
    data["loaders"] = sorted({loader for p in parsed for loader in p["loaders"]})
    data["dependencies"] = [Dependency(*item) for item in sorted({
        (d[0], d[1]) for p in parsed for d in p["dependencies"] if d[0] not in BUILTINS
    })]
    # A universal JAR must not inherit compatibility from just its first loader.
    versions = [exact_versions(p.get("minecraft", "")) for p in parsed]
    data.pop("minecraft", None)
    data["game_versions"] = versions[0] if all(v == versions[0] for v in versions) else []
    for key in ("environment", "license"):
        if len({p[key] for p in parsed}) > 1:
            data[key] = ""
    for key in ("game_versions", "environment", "release_id"):
        if key in overrides:
            data[key] = overrides[key]
    if not isinstance(data["game_versions"], list) or not all(isinstance(v, str) and v for v in data["game_versions"]):
        raise CourierError("invalid_config", "game_versions must be an array of exact version strings.")
    if data.get("release_id") and not re.fullmatch(r"[A-Za-z0-9_.+-]+", data["release_id"]):
        raise CourierError("invalid_config", "release_id must contain only letters, digits, dots, hyphens, underscores and plus signs.")
    if data.get("release_id") in RESERVED_RELEASE_IDS:
        raise CourierError("invalid_config", "release_id is reserved for a journal operation; choose another release ID.")
    artifact = Artifact(path=path.resolve(), hashes=file_hashes(path), **data)
    if not artifact.version or ("$" + "{") in artifact.version:
        raise CourierError("unresolved_version", f"{path.name}: build the release JAR so its version is resolved.")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", artifact.version):
        raise CourierError("invalid_version", f"{path.name}: version contains unsupported characters.")
    return artifact
