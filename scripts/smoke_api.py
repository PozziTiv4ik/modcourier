"""Optional read-only contract check against real public Modrinth endpoints."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from modcourier.http import Http


def main():
    client = Http("https://api.modrinth.com/v2")
    results = []
    for path, field in (("/tag/game_version", "version"), ("/tag/loader", "name"),
                        ("/tag/category", "name"), ("/tag/license", "short")):
        data = client.get(path)
        if not isinstance(data, list) or not data or field not in data[0]:
            raise RuntimeError(f"Unexpected public API shape: {path}")
        results.append({"endpoint": path, "records": len(data), "shape": "verified"})
    print(json.dumps({"read_only": True, "results": results}, indent=2))


if __name__ == "__main__":
    main()
