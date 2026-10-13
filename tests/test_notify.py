"""ntfy 通知(Notifier)とアプリ化用ファイルの配信のテスト。ntfy の代わりに手元の HTTP サーバーで受ける。"""

import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Auth, Broadcaster, HubSource, Notifier, QuietServer, make_handler  # noqa: E402


class FakeNtfy:
    def __init__(self):
        self.received = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.received.append({"path": self.path, "auth": self.headers.get("Authorization"), "json": json.loads(body)})
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def wait(self, n, timeout=3):
        end = time.time() + timeout
        while len(self.received) < n and time.time() < end:
            time.sleep(0.05)
        return self.received

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def sess(status, last="assistant_tool_use", sid="s1"):
    return {"id": sid, "status": status, "lastKind": last, "title": "グラフを直す", "projectName": "web-app"}


class NotifierTest(unittest.TestCase):
    def setUp(self):
        self.ntfy = FakeNtfy()

    def tearDown(self):
        self.ntfy.close()

    def test_transitions(self):
        n = Notifier(self.ntfy.base + "/my-topic", token="tk_abc", public_url="https://pi.example.ts.net/")
        n.observe([sess("waiting")])          # 起動直後に読んだ分は通知しない
        n.observe([sess("running")])
        n.observe([sess("waiting")])          # 作業中 → 入力待ち: 通知
        n.observe([sess("waiting")])          # 変化なし
        n.observe([sess("running")])
        n.observe([sess("ended", "assistant_end")])  # 作業中 → 終了(応答完了): 通知
        n.observe([sess("running")])
        n.observe([sess("interrupted")])      # 中断は自分で止めたので通知しない
        got = self.ntfy.wait(2)
        time.sleep(0.3)
        self.assertEqual(len(self.ntfy.received), 2)
        got = sorted(got, key=lambda r: r["json"]["title"] != "Claude Code: 入力待ちになりました")  # 送信は別スレッドなので順不同
        first = got[0]
        self.assertEqual(first["path"], "/")
        self.assertEqual(first["auth"], "Bearer tk_abc")
        self.assertEqual(first["json"]["topic"], "my-topic")
        self.assertEqual(first["json"]["title"], "Claude Code: 入力待ちになりました")
        self.assertIn("グラフを直す", first["json"]["message"])
        self.assertEqual(first["json"]["click"], "https://pi.example.ts.net/#session=s1")
        self.assertEqual(got[1]["json"]["title"], "Claude Code: 応答が終わりました")

    def test_private_hides_title(self):
        n = Notifier(self.ntfy.base + "/t", private=True)
        n.observe([sess("running")])
        n.observe([sess("error")])
        got = self.ntfy.wait(1)
        self.assertNotIn("グラフ", json.dumps(got[0]["json"], ensure_ascii=False))
        self.assertEqual(got[0]["json"]["title"], "Claude Code: エラーで止まりました")

    def test_bad_url(self):
        with self.assertRaises(ValueError):
            Notifier("https://ntfy.sh/")

    def test_hub_push_triggers_notification_and_app_files_without_login(self):
        bus = Broadcaster()
        hub = HubSource(bus, None, 120, Notifier(self.ntfy.base + "/t"))
        httpd = QuietServer(("127.0.0.1", 0), make_handler(hub, bus, Auth("pw", "x" * 20), False))
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            hub.push({"sessions": [sess("running")]})
            hub.push({"sessions": [sess("waiting")]})
            self.assertEqual(len(self.ntfy.wait(1)), 1)
            # アプリ化に必要なファイルはログイン無しで取れる。履歴の API はログインが必要なまま
            for path, ctype in [("/manifest.webmanifest", "application/manifest+json"), ("/sw.js", "text/javascript"),
                                ("/static/icons/icon-192.png", "image/png")]:
                with urllib.request.urlopen(base + path) as r:
                    self.assertTrue(r.headers["Content-Type"].startswith(ctype), (path, r.headers["Content-Type"]))
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/api/state")
            self.assertEqual(cm.exception.code, 401)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
