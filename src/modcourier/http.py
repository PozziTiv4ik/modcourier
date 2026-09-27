"""Small, bounded HTTP transport. Mutations are never blindly retried."""
from contextlib import contextmanager
from http.client import HTTPException
import hashlib
import json
from pathlib import Path
import re
import socket
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from . import __version__
from .errors import CourierError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward author credentials, or replay a POST, at another URL.
        return None


class Http:
    def __init__(self, base, headers=None, *, timeout=45, retries=2, sleeper=time.sleep):
        parsed = urlsplit(base)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise CourierError("insecure_url", "API connections require HTTPS.")
        self.base = base.rstrip("/")
        self.headers = dict(headers or {})
        self.timeout, self.retries, self.sleep = timeout, retries, sleeper
        self.opener = build_opener(NoRedirect())

    def clean(self, message):
        for key, secret in self.headers.items():
            if key.lower() in {"authorization", "x-api-key", "x-api-token"} and secret:
                message = message.replace(secret, "[REDACTED]")
        return message

    def request(self, method, path, *, data=None, content_type=None, length=None, missing_ok=False):
        if not path.startswith("/") or path.startswith("//"):
            raise CourierError("invalid_path", "API paths must be relative to the fixed platform endpoint.")
        headers = {
            "User-Agent": f"PozziTiv4ik/modcourier/{__version__} (github.com/PozziTiv4ik/modcourier)",
            "Accept": "application/json", **self.headers,
        }
        if content_type:
            headers["Content-Type"] = content_type
        if length is not None:
            headers["Content-Length"] = str(length)
        attempts = self.retries + 1 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                request = Request(self.base + path, data=data, headers=headers, method=method)
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise CourierError("response_size", "API response exceeds 16 MiB.", uncertain=method != "GET")
                if not raw:
                    if method == "GET":
                        raise CourierError("invalid_response", "API returned an empty response to a lookup.")
                    return None
                try:
                    value = json.loads(raw)
                    if value is None and method == "GET":
                        raise CourierError("invalid_response", "API returned null instead of lookup data.")
                    return value
                except (ValueError, UnicodeDecodeError) as exc:
                    raise CourierError("invalid_response", "API returned invalid JSON.", uncertain=method != "GET") from exc
            except HTTPError as exc:
                if exc.code == 404 and missing_ok:
                    exc.close()
                    return None
                retryable = exc.code == 429 or exc.code >= 500
                if retryable and attempt + 1 < attempts:
                    retry_after = exc.headers.get("Retry-After", "")
                    delay = min(10, int(retry_after)) if retry_after.isdigit() else 2 ** attempt
                    exc.close()
                    self.sleep(delay)
                    continue
                try:
                    raw = exc.read(2048).decode("utf-8", "replace")
                except (OSError, HTTPException):
                    # The HTTP status still tells us whether the write may have succeeded.
                    raw = ""
                finally:
                    exc.close()
                try:
                    body = json.loads(raw)
                    raw = str(body.get("description") or body.get("errorMessage") or body.get("message") or raw)
                except (ValueError, AttributeError):
                    raw = re.sub("<[^>]*>", "", raw)
                reason = self.clean(raw).strip()[:400]
                code = "authentication" if exc.code in {401, 403} else f"http_{exc.code}"
                raise CourierError(code, f"HTTP {exc.code}: {reason or 'request failed'}",
                                   uncertain=method != "GET" and (exc.code >= 500 or exc.code == 408 or 300 <= exc.code < 400)) from exc
            except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError, HTTPException) as exc:
                if attempt + 1 < attempts:
                    self.sleep(2 ** attempt)
                    continue
                raise CourierError("network", self.clean(f"Connection failed: {exc}"),
                                   uncertain=method != "GET") from exc

    def get(self, path, *, missing_ok=False):
        return self.request("GET", path, missing_ok=missing_ok)

    def json(self, method, path, value):
        data = json.dumps(value).encode()
        return self.request(method, path, data=data, content_type="application/json", length=len(data))

    def multipart(self, path, fields, files=()):
        with multipart_body(fields, files) as (body, content_type, length):
            return self.request("POST", path, data=body, content_type=content_type, length=length)


@contextmanager
def multipart_body(fields, files=()):
    boundary = "modcourier-" + uuid.uuid4().hex
    with tempfile.TemporaryFile() as body:
        for name, value in fields.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
                raise CourierError("invalid_part", "Invalid multipart field name.")
            body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.write((value if isinstance(value, str) else json.dumps(value)).encode("utf-8"))
            body.write(b"\r\n")
        for name, path, expected_hash in files:
            path = Path(path)
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", name) or any(c in path.name for c in '"\r\n\\'):
                raise CourierError("invalid_filename", "File names must not contain quotes, newlines or backslashes.")
            body.write((
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8"))
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    body.write(chunk)
            if expected_hash and digest.hexdigest() != expected_hash:
                raise CourierError("artifact_changed", f"{path.name} changed since inspection; inspect again.")
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        length = body.tell()
        body.seek(0)
        yield body, f"multipart/form-data; boundary={boundary}", length
