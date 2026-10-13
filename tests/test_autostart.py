"""setup_autostart.py / autostart.py のテスト(一時的なホームフォルダで実行し、本物の設定には触れない)。"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        self.settings = self.home / ".claude" / "settings.json"
        self.settings.parent.mkdir(parents=True)
        # 既存の設定(他のフックや項目)は残らなければならない
        self.settings.write_text(json.dumps({
            "model": "x",
            "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo other"}]},
                                       # 旧フォルダ名で登録した古い設定(install で置き換わること)
                                       {"matcher": "startup|resume", "hooks": [{"type": "command", "command": "python3",
                                                                                "args": ["/Users/x/claude_code_program/autostart.py"]}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]},
        }))
        self.procs_before = self._our_pids()

    def tearDown(self):
        for pid in self._our_pids() - self.procs_before:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        self.tmp.cleanup()

    def _our_pids(self):
        out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
        marks = [str(self.home)] + [f"server.py --port {p}" for p in getattr(self, "ports", [])]
        return {int(l.split()[0]) for l in out.splitlines()[1:] if any(m in l for m in marks)}

    def run_py(self, *args):
        return subprocess.run([sys.executable, *map(str, args)], env=self.env, capture_output=True, text=True, timeout=30)


class AutostartTest(Base):
    def test_install_merge_and_uninstall(self):
        r = self.run_py(ROOT / "setup_autostart.py", "install", "--port", "18999")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.run_py(ROOT / "setup_autostart.py", "install", "--port", "18999")  # 2回目でも登録は1つ
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["model"], "x")
        self.assertIn("Stop", data["hooks"])
        commands = [h.get("command") for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
        ours = [h for g in data["hooks"]["SessionStart"] for h in g["hooks"] if h.get("args") == [str(ROOT / "autostart.py")]]
        self.assertIn("echo other", commands)
        self.assertEqual(len(ours), 1)
        self.assertNotIn("/Users/x/claude_code_program/autostart.py", json.dumps(data))
        self.assertTrue(list(self.settings.parent.glob("settings.json.bak-*")))
        cfg = self.home / ".claude-code-program" / "config.json"
        self.assertEqual(oct(cfg.stat().st_mode & 0o777), "0o600")

        r = self.run_py(ROOT / "setup_autostart.py", "uninstall")
        data = json.loads(self.settings.read_text())
        self.assertEqual([h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]], ["echo other"])
        self.assertIn("Stop", data["hooks"])

    def test_autostart_starts_once(self):
        port = free_port()
        self.ports = [port]
        (self.home / ".claude-code-program").mkdir()
        hub_port = free_port()
        (self.home / ".claude-code-program" / "config.json").write_text(json.dumps(
            {"viewer": True, "port": port, "hub": f"http://127.0.0.1:{hub_port}", "token": "t" * 20, "allow_http": True}))
        t0 = time.time()
        r = self.run_py(ROOT / "autostart.py")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")  # 標準出力は Claude の文脈に入るので空であること
        self.assertLess(time.time() - t0, 5)  # すぐ戻ること
        for _ in range(50):
            if port_open(port):
                break
            time.sleep(0.1)
        self.assertTrue(port_open(port), "画面サーバーが起動していない")
        time.sleep(1)
        first = self._our_pids() - self.procs_before
        self.run_py(ROOT / "autostart.py")  # 2回目は何も起動しない
        time.sleep(1.5)
        second = self._our_pids() - self.procs_before
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)  # server.py と sync_client.py


class OpenViewerTest(Base):
    def test_install_merges_config(self):
        self.run_py(ROOT / "setup_autostart.py", "install", "--hub", "https://hub.example", "--token", "t" * 20)
        self.run_py(ROOT / "setup_autostart.py", "install", "--open", "app")  # 2回目は指定した項目だけ変わる
        cfg = json.loads((self.home / ".claude-code-program" / "config.json").read_text())
        self.assertEqual((cfg["hub"], cfg["open"]), ("https://hub.example", "app"))
        self.run_py(ROOT / "setup_autostart.py", "install", "--no-hub")
        cfg = json.loads((self.home / ".claude-code-program" / "config.json").read_text())
        self.assertNotIn("hub", cfg)
        self.assertEqual(cfg["open"], "app")

    def test_opens_only_when_no_viewer(self):
        port = free_port()
        self.ports = [port]
        dry = self.home / "opened.txt"
        self.env["CCP_OPEN_DRYRUN"] = str(dry)
        (self.home / ".claude-code-program").mkdir()
        (self.home / ".claude-code-program" / "config.json").write_text(json.dumps({"viewer": True, "port": port, "open": "browser"}))
        self.run_py(ROOT / "autostart.py")  # サーバーを起動 → 再接続待ち(5秒)→ 画面が無いので開く
        for _ in range(100):
            if dry.exists():
                break
            time.sleep(0.1)
        self.assertEqual(json.loads(dry.read_text().splitlines()[0]), ["<browser>", f"http://127.0.0.1:{port}/"])

        # 画面が1つ開いている(リアルタイム更新につながっている)状態では開かない
        stream = socket.create_connection(("127.0.0.1", port))
        stream.sendall(b"GET /api/events HTTP/1.1\r\nHost: x\r\n\r\n")
        stream.recv(100)  # ヘッダーが返ってくれば接続済み
        time.sleep(0.5)
        dry.unlink()
        self.run_py(ROOT / "autostart.py")
        time.sleep(3)
        self.assertFalse(dry.exists())
        log = (self.home / ".claude-code-program" / "logs" / "open.log").read_text()
        self.assertIn("すでに 1 個開いている", log)
        stream.close()


if __name__ == "__main__":
    unittest.main()
