"""hub モード(認証・受信・未接続判定)のテスト。"""

import gzip
import http.cookiejar
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Auth, Broadcaster, HubSource, make_handler  # noqa: E402

PASSWORD = "test-password"
TOKEN = "t" * 32


class HubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bus = Broadcaster()
        self.hub = HubSource(self.bus, Path(self.tmp.name) / "data.json", stale_sec=1.0)
        handler = make_handler(self.hub, self.bus, Auth(PASSWORD, TOKEN), False)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def get(self, path, opener=None):
        try:
            with (opener or self.opener).open(self.base + path, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def push(self, payload, token=TOKEN):
        req = urllib.request.Request(self.base + "/api/push", data=gzip.compress(json.dumps(payload).encode()), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def login(self, password):
        data = urllib.parse.urlencode({"password": password}).encode()
        try:
            with self.opener.open(self.base + "/login", data=data, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_requires_login(self):
        self.assertEqual(self.get("/api/state")[0], 401)
        self.assertEqual(self.login("wrong"), 401)
        self.assertEqual(self.get("/api/state")[0], 401)
        self.assertEqual(self.login(PASSWORD), 200)  # ログイン後は / へ転送される
        code, body = self.get("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["mode"], "hub")

    def test_push_requires_token_and_stale_becomes_unknown(self):
        self.assertEqual(self.push({"sessions": []}, token="x" * 32)[0], 401)
        sess = {"id": "s1", "status": "running", "statusSource": "process", "title": "作業"}
        code, res = self.push({"clientName": "mac", "sessions": [sess], "logs": {"s1": [{"ts": 1, "kind": "prompt", "text": "やって"}]}})
        self.assertEqual(code, 200)
        self.assertEqual(res["received"], 1)
        self.login(PASSWORD)
        state = json.loads(self.get("/api/state")[1])
        self.assertTrue(state["online"])
        self.assertEqual(state["sessions"][0]["status"], "running")
        self.assertEqual(json.loads(self.get("/api/session/s1/log")[1])["events"][0]["text"], "やって")
        time.sleep(1.2)  # stale_sec を過ぎると Mac 未接続
        state = json.loads(self.get("/api/state")[1])
        self.assertFalse(state["online"])
        self.assertEqual(state["sessions"][0]["status"], "unknown")

    def test_data_persisted(self):
        self.push({"sessions": [{"id": "s1", "status": "ended"}]})
        self.hub._save()
        again = HubSource(self.bus, Path(self.tmp.name) / "data.json", stale_sec=1.0)
        self.assertEqual(list(again.sessions), ["s1"])
        self.assertNotEqual(again.instance, self.hub.instance)


if __name__ == "__main__":
    unittest.main()
