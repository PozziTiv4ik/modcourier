from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from .errors import CourierError
from .models import RemoteFile


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    try:
        with path.open(encoding="utf-8-sig") as stream:
            return json.load(stream)
    except (ValueError, OSError) as exc:
        raise CourierError("invalid_json", f"Cannot read {path.name}: {exc}") from exc


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class State:
    def __init__(self, root: Path):
        self.directory = root / ".modcourier"
        self.path = self.directory / "state.json"
        self.data = read_json(self.path, {"schema_version": 1, "operations": {}, "projects": {}})
        if (not isinstance(self.data, dict) or type(self.data.get("schema_version")) is not int
            or self.data.get("schema_version") != 1):
            raise CourierError("state_version", "Unsupported state version; keep the journal and upgrade ModCourier.")
        if not isinstance(self.data.get("operations"), dict) or not isinstance(self.data.get("projects"), dict):
            raise CourierError("invalid_state", "Invalid journal. Restore it before publishing.")
        if any(not isinstance(v, str) or not v for v in self.data["projects"].values()):
            raise CourierError("invalid_state", "Invalid project binding in the journal. Restore it before publishing.")
        for key, operation in self.data["operations"].items():
            if (not re.fullmatch(r"[a-z][a-z0-9_]*:[A-Za-z0-9_.+-]+", key)
                or not isinstance(operation, dict)
                or not isinstance(operation.get("status"), str)
                or operation["status"] not in {"started", "accepted", "uncertain", "failed"}
                or ("project_id" in operation and (not isinstance(operation["project_id"], str) or not operation["project_id"]))
                or ("sha256" in operation and (not isinstance(operation["sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", operation["sha256"])))
                or ("error" in operation and (not isinstance(operation["error"], dict)
                    or not all(isinstance(operation["error"].get(field), str) for field in ("code", "message"))))):
                raise CourierError("invalid_state", "Invalid operation in the journal. Restore it before publishing.")
            if operation.get("receipt") is not None:
                try:
                    RemoteFile(**operation["receipt"])
                    if not operation.get("project_id"):
                        raise ValueError("Missing project ID")
                except (CourierError, TypeError, ValueError) as exc:
                    raise CourierError("invalid_state", "Invalid upload receipt in the journal. Restore it before publishing.") from exc

    def operation(self, key):
        return self.data["operations"].get(key, {})

    def record(self, key, **values):
        entry = dict(self.operation(key))
        if values.get("status") in {"started", "accepted"}:
            entry.pop("error", None)
        if values.get("status") == "started":
            entry.pop("receipt", None)
        entry.update(values, updated_at=now())
        self.data["operations"][key] = entry
        atomic_json(self.path, self.data)

    def bind(self, platform, project_id):
        self.data["projects"][platform] = str(project_id)
        atomic_json(self.path, self.data)

    @contextmanager
    def lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "publish.lock"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise CourierError(
                "locked", "Another publish holds .modcourier/publish.lock. If a process crashed, "
                "confirm it has stopped before removing only that lock file; keep state.json."
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "started_at": now()}, stream)
            yield
        finally:
            path.unlink(missing_ok=True)
