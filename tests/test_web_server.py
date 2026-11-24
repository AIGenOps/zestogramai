import unittest
import threading
import socket
import urllib.request
import urllib.error
import json
from http.server import ThreadingHTTPServer
from src.web_server import ZestogramWebHandler

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

class TestWebServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = get_free_port()
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), ZestogramWebHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        # Direct connection without environment proxy for localhost
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_health_check(self):
        req = urllib.request.Request(f"{self.base_url}/health")
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"OK")
            self.assertEqual(resp.headers.get("Content-Length"), "2")

    def test_cloudflare_keepalive_user_agent(self):
        req = urllib.request.Request(
            f"{self.base_url}/",
            headers={"User-Agent": "CloudflareWorker-KeepAlive/1.0"}
        )
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"OK")

    def test_browser_get_root(self):
        req = urllib.request.Request(
            f"{self.base_url}/",
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        )
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers.get("Content-Type"))
            body = resp.read().decode("utf-8")
            self.assertIn("Zestogram Downloader", body)
            self.assertIn("download-form", body)

    def test_post_invalid_url(self):
        req = urllib.request.Request(
            f"{self.base_url}/api/download",
            data=json.dumps({"url": "invalid-url"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with self.opener.open(req) as resp:
                self.fail("Should have returned 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            data = json.loads(e.read().decode("utf-8"))
            self.assertFalse(data.get("success"))
            self.assertIn("error", data)

    def test_options_cors(self):
        req = urllib.request.Request(
            f"{self.base_url}/api/download",
            method="OPTIONS"
        )
        with self.opener.open(req) as resp:
            self.assertEqual(resp.status, 204)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")

if __name__ == "__main__":
    unittest.main()
