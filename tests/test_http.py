from pathlib import Path
import tempfile
import unittest

from tests.helpers import service
from modcourier.errors import CourierError
from modcourier.http import Http, multipart_body


class HttpTests(unittest.TestCase):
    def test_get_retries_transient_errors(self):
        with service() as server:
            self.assertEqual(Http(server.url, sleeper=lambda _: None).get("/retry"), {"ok": True})
            self.assertEqual(len(server.calls), 3)

    def test_post_is_never_blindly_retried(self):
        with service() as server:
            with self.assertRaises(CourierError) as error:
                Http(server.url, sleeper=lambda _: None).json("POST", "/retry", {})
            self.assertTrue(error.exception.uncertain)
            self.assertEqual(len(server.calls), 1)

    def test_redirects_do_not_forward_credentials(self):
        with service() as server:
            with self.assertRaises(CourierError):
                Http(server.url, {"Authorization": "private-value"}).get("/redirect")
            self.assertEqual(server.calls, [("GET", "/redirect")])

    def test_error_redacts_token(self):
        with service() as server:
            with self.assertRaises(CourierError) as error:
                Http(server.url, {"Authorization": "private-value"}).get("/secrets")
            self.assertNotIn("private-value", str(error.exception))

    def test_multipart_checks_snapshot_before_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.jar"
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(CourierError, "changed since inspection"):
                with multipart_body({"data": {}}, [("file", path, "0" * 64)]):
                    self.fail("A changed file must not produce an upload body")

    def test_untrusted_endpoint_paths_rejected(self):
        client = Http("https://api.modrinth.com")
        for path in ("https://example.com", "//example.com"):
            with self.assertRaises(CourierError):
                client.get(path)

    def test_insecure_non_local_endpoints_rejected(self):
        with self.assertRaises(CourierError):
            Http("http://example.com")
