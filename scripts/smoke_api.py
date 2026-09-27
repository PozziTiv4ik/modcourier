"""Optional read-only contract check against real public Modrinth endpoints."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from modcourier.http import Http
from modcourier.config import Config
from modcourier.connectors.modrinth import Modrinth


def main():
    client = Http("https://api.modrinth.com/v2")
    results = []
    for path, field in (("/tag/game_version", "version"), ("/tag/loader", "name"),
                        ("/tag/category", "name"), ("/tag/license", "short")):
        data = client.get(path)
        if not isinstance(data, list) or not data or field not in data[0]:
            raise RuntimeError(f"Unexpected public API shape: {path}")
        results.append({"endpoint": path, "records": len(data), "shape": "verified"})
    connector = Modrinth(Config(Path.cwd(), {}), http=client)
    project = connector.get_project("fabric-api")
    if project is None or not project.raw.get("versions"):
        raise RuntimeError("The public Fabric API project or its versions could not be read.")
    file_id = project.raw["versions"][-1]
    files = connector.files_for(project, file_id)
    if not files or not all(file.hashes.get("sha512") for file in files):
        raise RuntimeError("The public Modrinth version did not provide verifiable files.")
    results.extend([
        {"endpoint": "/project/fabric-api", "project_id": project.id, "shape": "verified"},
        {"endpoint": "/version/" + file_id, "files": len(files), "shape": "verified"},
    ])
    print(json.dumps({"read_only": True, "results": results}, indent=2))


if __name__ == "__main__":
    main()
