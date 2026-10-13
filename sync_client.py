"""Mac の Claude Code 履歴を、常時稼働サーバー(server.py --mode hub)へ送り続ける。

    python3 sync_client.py --hub https://example.com --token <PUSH_TOKEN>

- 履歴ファイルの追記分だけを読み、表示内容が変わったセッションの要約と会話ログを送る。
- 変化が無くても30秒ごとに生存通知を送る(サーバー側はこれが途絶えると「Mac 未接続」と表示する)。
- サーバーが再起動して記録を失っていたら(応答の instance が変わったら)、全件を送り直す。
- 送信に失敗したら、間隔を空けて再送する。元の履歴ファイルは読むだけで書き換えない。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from history_parser import HistoryStore
from sync_lock import try_lock

HEARTBEAT_SEC = 30
BATCH_SESSIONS = 25
LOG_LIMIT = 300


class HubClient:
    def __init__(self, hub: str, token: str, insecure: bool = False):
        self.url = hub.rstrip("/") + "/api/push"
        self.token = token
        self.ctx = ssl._create_unverified_context() if insecure else None

    def push(self, payload: dict) -> dict:
        body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        req = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "claude-code-program-sync/1.1",
            },
        )
        with urllib.request.urlopen(req, timeout=60, context=self.ctx) as res:
            return json.loads(res.read().decode("utf-8"))


def run(args):
    token = args.token or os.environ.get("PUSH_TOKEN")
    if not token:
        sys.exit("送信用トークンを --token または環境変数 PUSH_TOKEN で指定してください")
    if args.hub.startswith("http://") and not args.allow_http:
        sys.exit("http:// では会話内容とトークンが暗号化されずに送られます。https:// の URL を使ってください(試験用途なら --allow-http)")

    if args.lock_file:
        lock = try_lock(Path(args.lock_file).expanduser())
        if lock is None:
            print("同期はすでに別のプロセスで動いているので終了します")
            return
        run.lock = lock  # プロセスが終わるまでロックを持ち続ける

    store = HistoryStore(Path(args.claude_dir).expanduser())
    client = HubClient(args.hub, token, args.insecure)
    name = args.name or socket.gethostname()
    last_sent: dict[str, str] = {}
    instance = None
    last_push = 0.0
    backoff = args.interval

    print(f"同期を開始します: {store.projects_dir} → {args.hub}")
    while True:
        try:
            store.scan()
            store.scan_live()
            summaries = store.all_summaries()
            changed = []
            for s in summaries:
                key = json.dumps(s, sort_keys=True, ensure_ascii=False)
                if last_sent.get(s["id"]) != key:
                    changed.append((s, key))

            if changed or time.time() - last_push >= HEARTBEAT_SEC:
                batches = [changed[i : i + BATCH_SESSIONS] for i in range(0, len(changed), BATCH_SESSIONS)] or [[]]
                for batch in batches:
                    payload = {"clientName": name, "sessions": [s for s, _ in batch]}
                    if not args.no_logs:
                        payload["logs"] = {s["id"]: store.session_log(s["id"], LOG_LIMIT) for s, _ in batch}
                    res = client.push(payload)
                    if instance is not None and res.get("instance") != instance:
                        # サーバーが再起動した。記録が失われている可能性があるので、次の周回で全件を送り直す
                        print("サーバーの再起動を検知したので、全件を送り直します")
                        last_sent.clear()
                        instance = res.get("instance")
                        break
                    instance = res.get("instance")
                    for s, key in batch:
                        last_sent[s["id"]] = key
                last_push = time.time()
                if changed:
                    print(f"{time.strftime('%H:%M:%S')} {len(changed)} セッションを送信しました")
            backoff = args.interval
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                sys.exit("トークンが受け付けられませんでした(401)。サーバーの PUSH_TOKEN と同じ値か確認してください")
            print(f"送信失敗(HTTP {exc.code})。{backoff:.0f}秒後に再試行します")
            backoff = min(backoff * 2, 300)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"送信失敗({exc})。{backoff:.0f}秒後に再試行します")
            backoff = min(backoff * 2, 300)
        time.sleep(backoff)


def main():
    ap = argparse.ArgumentParser(description="Claude Code の履歴を常時稼働サーバーへ同期する")
    ap.add_argument("--hub", required=True, help="サーバーの URL(例: https://example.com)")
    ap.add_argument("--token", default=None, help="送信用トークン(既定: 環境変数 PUSH_TOKEN)")
    ap.add_argument("--claude-dir", default=str(Path.home() / ".claude"), help="Claude Code の設定ディレクトリ")
    ap.add_argument("--interval", type=float, default=5, help="確認間隔(秒)")
    ap.add_argument("--name", default=None, help="画面に表示する送信元の名前(既定: ホスト名)")
    ap.add_argument("--no-logs", action="store_true", help="会話ログを送らない(要約だけ送る)")
    ap.add_argument("--allow-http", action="store_true", help="http:// の URL を許可する(試験用)")
    ap.add_argument("--insecure", action="store_true", help="HTTPS の証明書を検証しない(試験用)")
    ap.add_argument("--lock-file", default=None, help="二重起動を防ぐロックファイル(autostart.py が指定する)")
    run(ap.parse_args())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
