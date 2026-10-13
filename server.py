"""Claude Code 作業履歴ビューアのサーバー(Python 標準ライブラリのみ)。

動作モードは2つ:

  local(既定) … このパソコンの履歴(~/.claude)を直接読んで表示する。
      python server.py                       # http://127.0.0.1:8765

  hub … 常時稼働のサーバーで動かし、Mac の sync_client.py から送られてくる要約を保存・表示する。
        Mac の電源が切れていても、最後に同期した内容を外出先から見られる。
      VIEWER_PASSWORD=... PUSH_TOKEN=... python server.py --mode hub --host 0.0.0.0

- 変化したセッションは Server-Sent Events(/api/events)でブラウザへ送り、再読み込みなしで更新する。
- パスワード(--password または環境変数 VIEWER_PASSWORD)を設定するとログインが必要になる。
  hub モードではパスワードと送信用トークンの設定を必須にしている。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import secrets
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from history_parser import HistoryStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_PUSH_BYTES = 64 * 1024 * 1024
COOKIE_NAME = "ccp_session"
COOKIE_MAX_AGE = 30 * 86400


class Broadcaster:
    """SSE の購読者ごとにキューを持ち、更新を配る。"""

    def __init__(self):
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def publish(self, event: str, payload: dict):
        data = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        with self.lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(data)
                except queue.Full:
                    self.clients.discard(q)  # 受け取れていない購読者は切る(ブラウザが自動で再接続する)


# ---------------------------------------------------------------- 通知(ntfy)


class Notifier:
    """セッションの状態の変化を見て、ntfy(スマホ等へのプッシュ通知サービス)へ送る。

    通知する変化(作業中 → の変化だけ。起動直後に読み込んだ分は通知しない):
      作業中 → 入力待ち / 作業中 → エラー / 作業中 → 終了(プロセス情報が無く、応答の完了を履歴から判定した場合)
    """

    def __init__(self, url: str | None, token: str | None = None, public_url: str | None = None, private: bool = False):
        self.enabled = bool(url)
        self.token = token or ""
        self.public_url = (public_url or "").rstrip("/")
        self.private = private
        self.last: dict[str, str] = {}
        self.lock = threading.Lock()
        if self.enabled:
            parsed = urlparse(url)
            self.endpoint = f"{parsed.scheme}://{parsed.netloc}/"
            self.topic = parsed.path.strip("/")
            if not self.topic or "/" in self.topic:
                raise ValueError("ntfy の URL は https://ntfy.sh/<トピック名> の形で指定してください")

    @staticmethod
    def event_of(prev: str | None, s: dict) -> str | None:
        status = s.get("status")
        if prev != "running" or status == "running":
            return None
        if status == "waiting":
            return "入力待ちになりました"
        if status == "error":
            return "エラーで止まりました"
        if status == "ended" and s.get("lastKind") == "assistant_end":
            return "応答が終わりました"
        return None

    def observe(self, sessions: list) -> None:
        if not self.enabled:
            return
        events = []
        with self.lock:
            for s in sessions:
                sid = s.get("id")
                if not sid:
                    continue
                prev = self.last.get(sid)
                self.last[sid] = s.get("status")
                ev = self.event_of(prev, s)
                if ev:
                    events.append((ev, s))
        for ev, s in events:
            threading.Thread(target=self._send, args=(ev, s), daemon=True).start()

    def _send(self, event: str, s: dict) -> None:
        title = f"Claude Code: {event}"
        body = "セッションの状態が変わりました" if self.private else f"{s.get('title') or '(無題)'}({s.get('projectName') or '-'})"
        payload = {"topic": self.topic, "title": title, "message": body, "priority": 4 if event != "応答が終わりました" else 3}
        if self.public_url:
            payload["click"] = f"{self.public_url}/#session={s.get('id')}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                res.read()
            print(f"[通知] {title}: {body}")
        except Exception as exc:
            print(f"[通知] 送信に失敗しました: {exc!r}")


# ---------------------------------------------------------------- データ元


class LocalSource:
    """このパソコンの履歴ファイルを直接監視する。"""

    mode = "local"

    def __init__(self, store: HistoryStore, bus: Broadcaster, interval: float, notifier: Notifier | None = None):
        self.store, self.bus, self.interval = store, bus, interval
        self.notifier = notifier

    def start(self):
        self.store.scan()
        self.store.scan_live()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        last_sent: dict[str, str] = {}
        while True:
            try:
                self.store.scan()
                self.store.scan_live()
                changed = []
                for s in self.store.all_summaries():
                    key = json.dumps(s, sort_keys=True, ensure_ascii=False)
                    if last_sent.get(s["id"]) != key:
                        last_sent[s["id"]] = key
                        changed.append(s)
                if changed:
                    self.bus.publish("update", {"sessions": changed, **self.meta()})
                    if self.notifier:
                        self.notifier.observe(changed)
            except Exception as exc:  # 監視は止めない
                print(f"[watch] エラー: {exc!r}")
            time.sleep(self.interval)

    def meta(self) -> dict:
        return {"serverTime": time.time(), "mode": self.mode}

    def state(self) -> dict:
        return {"sessions": self.store.all_summaries(), "source": str(self.store.projects_dir), **self.meta()}

    def log(self, sid: str, limit: int) -> list:
        return self.store.session_log(sid, limit)


class HubSource:
    """sync_client.py から送られた要約を保持する(常時稼働サーバー用)。"""

    mode = "hub"

    def __init__(self, bus: Broadcaster, data_file: Path | None, stale_sec: float, notifier: Notifier | None = None):
        self.bus = bus
        self.notifier = notifier
        self.data_file = data_file
        self.stale_sec = stale_sec
        # 再起動を送信側に知らせるための ID。変わっていたら送信側が全件を送り直す
        self.instance = secrets.token_hex(8)
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}
        self.logs: dict[str, list] = {}
        self.last_push: float | None = None
        self.client_name = ""
        self.dirty = False
        self._was_online = False
        self._load()

    def _load(self):
        if not self.data_file or not self.data_file.exists():
            return
        try:
            data = json.loads(self.data_file.read_text(encoding="utf-8"))
            self.sessions = {s["id"]: s for s in data.get("sessions", [])}
            self.logs = data.get("logs", {})
            self.last_push = data.get("lastPush")
            self.client_name = data.get("clientName", "")
            print(f"保存データを読み込みました: {len(self.sessions)} セッション")
        except (OSError, ValueError, KeyError) as exc:
            print(f"保存データを読み込めませんでした: {exc!r}")

    def _save(self):
        if not self.data_file:
            return
        with self.lock:
            payload = {"sessions": list(self.sessions.values()), "logs": self.logs, "lastPush": self.last_push, "clientName": self.client_name}
            self.dirty = False
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.data_file.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, self.data_file)  # 書き込み途中で落ちても壊れないよう置き換えで保存する

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        """保存と、Mac との接続が切れたときの状態切り替えの通知を行う。"""
        while True:
            time.sleep(5)
            try:
                if self.dirty:
                    self._save()
                online = self.online()
                if online != self._was_online:
                    self._was_online = online
                    self.bus.publish("update", {"sessions": self.summaries(), **self.meta()})
            except Exception as exc:
                print(f"[hub] エラー: {exc!r}")

    def online(self) -> bool:
        return self.last_push is not None and time.time() - self.last_push < self.stale_sec

    def push(self, payload: dict) -> dict:
        sessions = payload.get("sessions") or []
        logs = payload.get("logs") or {}
        with self.lock:
            for s in sessions:
                if isinstance(s, dict) and s.get("id"):
                    self.sessions[s["id"]] = s
            for sid, events in logs.items():
                if isinstance(events, list):
                    self.logs[sid] = events
            self.last_push = time.time()
            self.client_name = str(payload.get("clientName") or self.client_name)[:100]
            self.dirty = self.dirty or bool(sessions or logs)
            changed_ids = [s["id"] for s in sessions if isinstance(s, dict) and s.get("id")]
        if self.notifier:
            self.notifier.observe([s for s in sessions if isinstance(s, dict)])
        became_online = not self._was_online
        self._was_online = True
        if became_online:
            self.bus.publish("update", {"sessions": self.summaries(), **self.meta()})
        elif changed_ids:
            self.bus.publish("update", {"sessions": self.summaries(changed_ids), **self.meta()})
        else:
            self.bus.publish("meta", self.meta())
        return {"ok": True, "instance": self.instance, "received": len(changed_ids)}

    def summaries(self, ids=None) -> list:
        online = self.online()
        with self.lock:
            items = [self.sessions[i] for i in ids if i in self.sessions] if ids is not None else list(self.sessions.values())
            out = []
            for s in items:
                if not online and s.get("status") in ("running", "waiting"):
                    # Mac から届かなくなったので、作業中かどうかは分からない
                    s = dict(s, status="unknown", statusSource="hub")
                out.append(s)
            return out

    def meta(self) -> dict:
        return {"serverTime": time.time(), "mode": self.mode, "lastPush": self.last_push, "online": self.online(), "clientName": self.client_name}

    def state(self) -> dict:
        return {"sessions": self.summaries(), "source": f"同期元: {self.client_name or '-'}", **self.meta()}

    def log(self, sid: str, limit: int) -> list:
        with self.lock:
            return list(self.logs.get(sid, []))[-limit:]


# ---------------------------------------------------------------- 認証


class Auth:
    """パスワードによるログイン。Cookie は「乱数.署名」で、パスワードを変えると全て無効になる。"""

    def __init__(self, password: str | None, push_token: str | None):
        self.password = password or ""
        self.push_token = push_token or ""
        self.key = hashlib.sha256(("ccp-cookie:" + self.password).encode("utf-8")).digest()
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def make_cookie(self) -> str:
        nonce = secrets.token_hex(16)
        sig = hmac.new(self.key, nonce.encode(), hashlib.sha256).hexdigest()
        return f"{nonce}.{sig}"

    def valid_cookie(self, value: str | None) -> bool:
        if not self.enabled:
            return True
        if not value or "." not in value:
            return False
        nonce, sig = value.split(".", 1)
        expected = hmac.new(self.key, nonce.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)

    def check_password(self, ip: str, password: str) -> str:
        """'ok' / 'ng' / 'locked' を返す。同じ IP からの失敗が続いたら一時的に受け付けない。"""
        now = time.time()
        with self.lock:
            recent = [t for t in self.failures.get(ip, []) if now - t < 600]
            self.failures[ip] = recent
            total = sum(1 for ts in self.failures.values() for t in ts if now - t < 600)
            # X-Forwarded-For は偽装できるので、IP ごとの制限に加えて全体の失敗回数でも止める
            if len(recent) >= 10 or total >= 50:
                return "locked"
        if hmac.compare_digest(password.encode("utf-8"), self.password.encode("utf-8")):
            return "ok"
        with self.lock:
            self.failures.setdefault(ip, []).append(now)
        time.sleep(1)  # 総当たりを遅くする
        return "ng"

    def valid_push(self, header: str | None) -> bool:
        if not self.push_token or not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].strip().encode("utf-8"), self.push_token.encode("utf-8"))


LOGIN_PAGE = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>ログイン - Claude Code 作業履歴</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--muted:#898781;--border:rgba(11,11,11,.12);--accent:#2a78d6;--critical:#d03b3b}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--border:rgba(255,255,255,.12);--accent:#3987e5}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Hiragino Sans",sans-serif}
form{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:24px;width:min(340px,calc(100vw - 32px));box-sizing:border-box}
h1{font-size:17px;margin:0 0 16px}input,button{font:inherit;width:100%;box-sizing:border-box;padding:10px;border-radius:6px}
input{border:1px solid var(--border);background:var(--page);color:var(--ink);margin-bottom:12px}
button{border:0;background:var(--accent);color:#fff;font-weight:600;cursor:pointer}.err{color:var(--critical);font-size:13px;margin:0 0 10px}
</style></head><body><form method="post" action="/login"><h1>Claude Code 作業履歴</h1>__ERROR__
<input type="password" name="password" placeholder="パスワード" autocomplete="current-password" autofocus required>
<button type="submit">ログイン</button></form></body></html>"""


# ---------------------------------------------------------------- HTTP


class QuietServer(ThreadingHTTPServer):
    """ブラウザが接続を切っただけの例外はログに出さない(タブを閉じる・再読み込みで普通に起きる)。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_handler(source, bus: Broadcaster, auth: Auth, secure_cookie: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ClaudeCodeProgram/1.1"

        def log_message(self, fmt, *args):  # アクセスログは出さない
            pass

        # ---- 応答の道具 ----
        def _send(self, code, body: bytes, ctype: str, headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _redirect(self, to, headers=None):
            self._send(303, b"", "text/plain", {"Location": to, **(headers or {})})

        def _cookie(self) -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE_NAME:
                    return v
            return None

        def _client_ip(self) -> str:
            fwd = self.headers.get("X-Forwarded-For")
            return fwd.split(",")[0].strip() if fwd else self.client_address[0]

        def _is_https(self) -> bool:
            return secure_cookie or self.headers.get("X-Forwarded-Proto", "").lower() == "https"

        def _authed(self) -> bool:
            return auth.valid_cookie(self._cookie())

        # ---- GET ----
        def do_GET(self):
            url = urlparse(self.path)
            path = unquote(url.path)
            if path == "/healthz":
                return self._send(200, b"ok", "text/plain")
            if path == "/login":
                if not auth.enabled:
                    return self._redirect("/")
                return self._send(200, LOGIN_PAGE.replace("__ERROR__", "").encode("utf-8"), "text/html; charset=utf-8")
            # アプリとして追加するためのファイル。ブラウザは Cookie を付けずに取りに来るのでログイン不要にする(中身に履歴は含まない)
            if path == "/manifest.webmanifest":
                return self._static("manifest.webmanifest", "application/manifest+json; charset=utf-8")
            if path == "/sw.js":
                return self._static("sw.js")
            if path.startswith("/static/icons/") or path == "/favicon.ico":
                return self._static("icons/favicon-32.png" if path == "/favicon.ico" else path[len("/static/"):])
            if path == "/logout":
                return self._redirect("/login", {"Set-Cookie": f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
            if not self._authed():
                if path.startswith("/api/"):
                    return self._json({"error": "unauthorized"}, 401)
                return self._redirect("/login")

            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/state":
                return self._json({**source.state(), "auth": auth.enabled})
            if path.startswith("/api/session/") and path.endswith("/log"):
                sid = path[len("/api/session/"): -len("/log")]
                try:
                    limit = max(1, min(2000, int(parse_qs(url.query).get("limit", ["400"])[0])))
                except ValueError:
                    limit = 400
                return self._json({"id": sid, "events": source.log(sid, limit)})
            if path == "/api/events":
                return self._sse()
            if path == "/api/viewers":
                # 開いている画面(リアルタイム更新でつながっているページ)の数。autostart.py が「すでに開いているか」の判断に使う
                with bus.lock:
                    n = len(bus.clients)
                return self._json({"viewers": n})
            self._send(404, b"not found", "text/plain; charset=utf-8")

        # ---- POST ----
        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_PUSH_BYTES:
                return self._json({"error": "too large"}, 413)
            body = self.rfile.read(length) if length else b""

            if path == "/login" and auth.enabled:
                password = parse_qs(body.decode("utf-8", "replace")).get("password", [""])[0]
                result = auth.check_password(self._client_ip(), password)
                if result == "ok":
                    flags = "; Secure" if self._is_https() else ""
                    cookie = f"{COOKIE_NAME}={auth.make_cookie()}; Path=/; Max-Age={COOKIE_MAX_AGE}; HttpOnly; SameSite=Lax{flags}"
                    return self._redirect("/", {"Set-Cookie": cookie})
                msg = "試行回数が多すぎます。10分後にやり直してください。" if result == "locked" else "パスワードが違います。"
                page = LOGIN_PAGE.replace("__ERROR__", f'<p class="err">{msg}</p>')
                return self._send(429 if result == "locked" else 401, page.encode("utf-8"), "text/html; charset=utf-8")

            if path == "/api/push" and isinstance(source, HubSource):
                if not auth.valid_push(self.headers.get("Authorization")):
                    return self._json({"error": "invalid token"}, 401)
                try:
                    if self.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    payload = json.loads(body.decode("utf-8"))
                except (OSError, ValueError, EOFError):
                    return self._json({"error": "bad payload"}, 400)
                return self._json(source.push(payload))
            self._send(404, b"not found", "text/plain; charset=utf-8")

        def _static(self, rel, ctype=None):
            target = (STATIC_DIR / rel).resolve()
            if STATIC_DIR not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            if ctype is None:
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.suffix == ".js":
                    ctype = "text/javascript"  # 環境によって推測結果が違うので固定する(Service Worker は JS の型が必須)
                if ctype.startswith("text/") or ctype in ("application/javascript",):
                    ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def _sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")  # 中継サーバーでのバッファリングを止める
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = bus.subscribe()
            try:
                self.wfile.write(b"retry: 3000\nevent: hello\ndata: {}\n\n")
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                    except queue.Empty:
                        data = b": keep-alive\n\n"
                    self.wfile.write(data)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                bus.unsubscribe(q)

    return Handler


def main():
    ap = argparse.ArgumentParser(description="Claude Code 作業履歴ビューア")
    ap.add_argument("--mode", choices=["local", "hub"], default="local", help="local: この PC の履歴を表示 / hub: 送られてきた要約を表示")
    ap.add_argument("--host", default=None, help="待ち受けアドレス(既定: local は 127.0.0.1、hub は 0.0.0.0)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")), help="待ち受けポート(既定: 環境変数 PORT または 8765)")
    ap.add_argument("--password", default=os.environ.get("VIEWER_PASSWORD"), help="閲覧用パスワード(既定: 環境変数 VIEWER_PASSWORD)")
    ap.add_argument("--secure-cookie", action="store_true", help="ログイン Cookie に Secure を付ける(HTTPS で公開するとき)")
    # local モード
    ap.add_argument("--claude-dir", default=str(Path.home() / ".claude"), help="[local] Claude Code の設定ディレクトリ")
    ap.add_argument("--projects-dir", default=None, help="[local] 履歴 jsonl のディレクトリ(既定: <claude-dir>/projects)")
    ap.add_argument("--interval", type=float, default=1.5, help="[local] 監視間隔(秒)")
    # 通知
    ap.add_argument("--ntfy-url", default=os.environ.get("NTFY_URL"), help="ntfy で通知する場合のトピック URL(例: https://ntfy.sh/<推測されにくい名前>)")
    ap.add_argument("--ntfy-token", default=os.environ.get("NTFY_TOKEN"), help="ntfy のアクセストークン(必要な場合。環境変数 NTFY_TOKEN でも可)")
    ap.add_argument("--public-url", default=os.environ.get("PUBLIC_URL"), help="通知をタップしたときに開くビューアの URL")
    ap.add_argument("--ntfy-private", action="store_true", help="通知にセッション名とプロジェクト名を入れない")
    # hub モード
    ap.add_argument("--push-token", default=os.environ.get("PUSH_TOKEN"), help="[hub] 送信用トークン(既定: 環境変数 PUSH_TOKEN)")
    ap.add_argument("--data-file", default=os.environ.get("DATA_FILE", "hub_data.json"), help="[hub] 受け取った要約の保存先")
    ap.add_argument("--stale-sec", type=float, default=120, help="[hub] この秒数送信が無ければ Mac 未接続とみなす")
    args = ap.parse_args()

    bus = Broadcaster()
    try:
        notifier = Notifier(args.ntfy_url, args.ntfy_token, args.public_url, args.ntfy_private)
    except ValueError as exc:
        ap.error(str(exc))
    if args.mode == "hub":
        if not args.password or not args.push_token:
            ap.error("hub モードでは閲覧用パスワード(VIEWER_PASSWORD)と送信用トークン(PUSH_TOKEN)の両方が必要です")
        if len(args.push_token) < 16:
            ap.error("PUSH_TOKEN は16文字以上にしてください")
        source = HubSource(bus, Path(args.data_file) if args.data_file else None, args.stale_sec, notifier)
        host = args.host or "0.0.0.0"
    else:
        store = HistoryStore(Path(args.claude_dir).expanduser(), Path(args.projects_dir).expanduser() if args.projects_dir else None)
        source = LocalSource(store, bus, args.interval, notifier)
        host = args.host or "127.0.0.1"
        if host not in ("127.0.0.1", "localhost", "::1") and not args.password:
            print("警告: パスワード無しで外部から接続できる状態です。--password の設定を勧めます。")
    source.start()

    auth = Auth(args.password, args.push_token)
    httpd = QuietServer((host, args.port), make_handler(source, bus, auth, args.secure_cookie))
    if args.mode == "local":
        print(f"履歴: {source.store.projects_dir}({len(source.store.sessions)} セッション)")
    else:
        print(f"hub モード: 保存先 {args.data_file}")
    if notifier.enabled:
        print(f"ntfy 通知: 有効(トピック {notifier.topic})")
    print(f"http://{host}:{args.port} で待ち受けています(Ctrl+C で終了)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
