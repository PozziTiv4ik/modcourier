from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from .errors import CourierError


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
        if not isinstance(self.data, dict) or self.data.get("schema_version") != 1:
            raise CourierError("state_version", "Unsupported state version; keep the journal and upgrade ModCourier.")
        if not isinstance(self.data.get("operations"), dict) or not isinstance(self.data.get("projects"), dict):
            raise CourierError("invalid_state", "Invalid journal. Restore it before publishing.")
        if any(not isinstance(v, dict) for v in self.data["operations"].values()):
            raise CourierError("invalid_state", "Invalid operation in the journal. Restore it before publishing.")

    def operation(self, key):
        return self.data["operations"].get(key, {})

    def record(self, key, **values):
        entry = dict(self.operation(key))
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
