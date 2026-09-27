from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qsl, urlsplit
import zipfile

from modcourier.config import Config
from modcourier.connectors.curseforge import CurseForge
from modcourier.connectors.modrinth import Modrinth
from modcourier.http import Http
from modcourier.publication import Publication


def jar(root, loader="fabric", version="1.0.0", name=None, payload=b"demo", minecraft="1.21.1", deps=None):
    target = root / "build" / "libs" / (name or f"demo-{version}-{loader}.jar")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("demo.class", payload)
        if loader == "fabric":
            archive.writestr("fabric.mod.json", json.dumps({
                "schemaVersion": 1, "id": "demo", "version": version, "name": "Demo",
                "description": "A test mod.", "license": "MIT", "environment": "client",
                "depends": {"minecraft": minecraft, **(deps or {})},
            }))
        elif loader == "quilt":
            archive.writestr("quilt.mod.json", json.dumps({"quilt_loader": {
                "id": "demo", "version": version,
                "metadata": {"name": "Demo", "description": "A test mod.", "license": "MIT"},
                "depends": [{"id": "minecraft", "versions": minecraft}],
            }}))
        else:
            file = "META-INF/neoforge.mods.toml" if loader == "neoforge" else "META-INF/mods.toml"
            archive.writestr(file, f'license="MIT"\n[[mods]]\nmodId="demo"\nversion="{version}"\ndisplayName="Demo"\n'
                            f'[[dependencies.demo]]\nmodId="minecraft"\nmandatory=true\nversionRange="[{minecraft}]"\n')
    return target


def config(root, *, bound=True):
    cfg = Config(root, {
        "schema_version": 1,
        "project": {"mod_id": "demo", "title": "Demo", "slug": "demo",
                    "summary": "A test mod.", "body": "A complete test description.",
                    "license": "MIT", "source_url": "https://github.com/author/demo"},
        "release": {"game_versions": ["1.21.1"], "environment": "client_only", "changelog": "Fix a bug."},
        "platforms": {
            "modrinth": {"categories": ["utility"], **({"project_id": "mr1"} if bound else {})},
            "curseforge": {"project_id": "42", "slug": "demo"},
        },
        "policy": {"ai_usage": "none"},
    })
    review_copy(cfg)
    return cfg


def review_copy(cfg):
    cfg.raw["publication"] = {"language": "en", "reviewed_sha256": Publication.read(cfg).sha256}


class Service:
    def __init__(self):
        self.mr_project = {"id": "mr1", "slug": "demo", "title": "Demo", "status": "approved",
                           "description": "A test mod.", "body": "A complete test description.",
                           "source_url": "https://github.com/author/demo"}
        self.mr_versions = []
        self.cf_files = []
        self.calls = []
        self.fail_cf = None
        self.accept_then_fail_cf = False
        self.cf_visible = True
        self.cf_project_id = 42
        self.mr_creates = 0
        self.mr_uploads = 0
        self.cf_uploads = 0
        self.pages = None
        self.cf_title = "Demo"
        self.cf_summary = "A test mod."
        self.cf_body = "A complete test description."
        self.cf_upload_metadata = []
        self.page_updates = 0
        self.lose_page_response = False

    @property
    def cf_project(self):
        return {"id": self.cf_project_id, "slug": "demo", "name": self.cf_title,
                "summary": self.cf_summary, "gameId": 432, "classId": 6,
                "status": 4, "authors": [{"name": "author"}], "links": {
                    "websiteUrl": "https://www.curseforge.com/minecraft/mc-mods/demo",
                    "sourceUrl": "https://github.com/author/demo",
                }}

    def route(self, method, path, headers, raw):
        self.calls.append((method, path))
        path_only = urlsplit(path).path
        if path_only == "/retry":
            count = sum(p == "/retry" for _, p in self.calls)
            return (503, {"message": "try again"}, {}) if count < 3 else (200, {"ok": True}, {})
        if path_only == "/redirect":
            return 302, {}, {"Location": "/other"}
        if path_only == "/secrets":
            return 401, {"message": "bad " + headers.get("Authorization", "")}, {}
        if path_only.startswith("/mr"):
            p = path_only.removeprefix("/mr")
            if method == "GET":
                if p == "/user":
                    return 200, {"id": "user1", "username": "author"}, {}
                if p == "/user/user1/projects":
                    return 200, [self.mr_project] if self.mr_project else [], {}
                if p == "/tag/game_version":
                    return 200, [{"version": "1.21.1"}], {}
                if p == "/tag/loader":
                    return 200, [{"name": v} for v in ("fabric", "forge", "neoforge", "quilt")], {}
                if p == "/tag/license":
                    return 200, [{"short": "MIT"}], {}
                if p == "/tag/category":
                    return 200, [{"name": "utility", "project_type": "mod"}], {}
                if p in {"/project/mr1", "/project/demo"}:
                    return (200, self.mr_project, {}) if self.mr_project else (404, {}, {})
                if p == "/project/mr1/version":
                    return 200, self.mr_versions, {}
            if method == "PATCH" and p == "/project/mr1":
                data = json.loads(raw)
                self.mr_project.update(data)
                if "body" in data:
                    self.page_updates += 1
                    if self.lose_page_response:
                        self.lose_page_response = False
                        return 503, {"message": "page saved, response lost"}, {}
                return 204, None, {}
            parts = parse_multipart(headers, raw) if raw else {}
            if method == "POST" and p == "/project":
                data = json.loads(parts["data"])
                self.mr_project = {"id": "mr1", "slug": data["slug"], "title": data["title"], "status": "draft",
                                   "description": data["description"], "body": data["body"],
                                   "source_url": data.get("source_url", "")}
                self.mr_creates += 1
                return 200, self.mr_project, {}
            if method == "POST" and p == "/version":
                data = json.loads(parts["data"])
                file = parts["file"]
                self.mr_uploads += 1
                result = {**data, "id": "v" + str(self.mr_uploads), "status": "listed",
                          "files": [{"filename": parts["filename"], "hashes": {
                              "sha512": hashlib.sha512(file).hexdigest(), "sha1": hashlib.sha1(file).hexdigest(),
                          }}]}
                self.mr_versions.append(result)
                return 200, result, {}
        if path_only == "/upload/game/versions":
            return 200, [{"id": i, "name": name, "gameVersionTypeID": 1}
                         for i, name in enumerate(["1.21.1", "Fabric", "Forge", "NeoForge", "Quilt", "Client", "Server"])], {}
        if path_only == "/upload/projects/42/upload-file":
            if self.fail_cf:
                return self.fail_cf, {"message": "simulated failure"}, {}
            parts = parse_multipart(headers, raw)
            data = json.loads(parts["metadata"])
            self.cf_upload_metadata.append(data)
            file = parts["file"]
            self.cf_uploads += 1
            result = {"id": 100 + self.cf_uploads, "displayName": data["displayName"], "fileName": parts["filename"],
                      "fileStatus": 1, "gameVersions": data["gameVersionNames"],
                      "hashes": [{"algo": 1, "value": hashlib.sha1(file).hexdigest()}]}
            self.cf_files.append(result)
            if self.accept_then_fail_cf:
                self.accept_then_fail_cf = False
                return 503, {"message": "response lost after acceptance"}, {}
            return 200, {"id": result["id"]}, {}
        if path_only == "/cf/v1/mods/42":
            return (200, {"data": self.cf_project}, {}) if self.cf_visible else (404, {}, {})
        if path_only == "/cf/v1/mods/42/description":
            return 200, {"data": self.cf_body}, {}
        if path_only == "/cf/v1/mods/42/files":
            if self.pages is not None:
                index = int(dict(parse_qsl(urlsplit(path).query)).get("index", 0))
                batch = self.pages[index:index + 50]
                return 200, {"data": batch, "pagination": {"index": index, "resultCount": len(batch), "totalCount": len(self.pages)}}, {}
            return 200, {"data": self.cf_files, "pagination": {"resultCount": len(self.cf_files), "totalCount": len(self.cf_files)}}, {}
        if path_only.startswith("/cf/v1/mods/42/files/"):
            file_id = int(path_only.split("/")[-1])
            data = next((f for f in self.cf_files if f["id"] == file_id), None)
            return (200, {"data": data}, {}) if data else (404, {}, {})
        if path_only == "/cf/v1/mods/search":
            return 200, {"data": [], "pagination": {"resultCount": 0, "totalCount": 0}}, {}
        return 404, {"message": "unknown fixture route: " + method + " " + path}, {}


def parse_multipart(headers, raw):
    message = BytesParser(policy=default).parsebytes(
        ("Content-Type: " + headers["Content-Type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw
    )
    parts = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        parts[name] = part.get_payload(decode=True)
        if part.get_filename():
            parts["filename"] = part.get_filename()
    return parts


@contextmanager
def service():
    state = Service()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): self.handle_request()
        def do_POST(self): self.handle_request()
        def do_PATCH(self): self.handle_request()

        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            code, value, headers = state.route(self.command, self.path, self.headers, raw)
            body = b"" if value is None else json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args): pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = "http://127.0.0.1:" + str(server.server_port)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def connectors(cfg, server):
    mr = Modrinth(cfg, Http(server.url + "/mr", {"Authorization": "test-token"}, sleeper=lambda _: None))
    cf = CurseForge(cfg, Http(server.url + "/cf", {"x-api-key": "test-key"}, sleeper=lambda _: None),
                    Http(server.url + "/upload", {"X-Api-Token": "test-upload"}, sleeper=lambda _: None))
    mr.token = "test-token"
    cf.token = "test-upload"
    cf.key = "test-key"
    return {"modrinth": mr, "curseforge": cf}
