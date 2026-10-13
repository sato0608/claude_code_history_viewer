"""Claude Code の起動時にビューアと同期を自動起動するよう登録する(Mac / Windows 共通)。

    python3 setup_autostart.py install                                  # 画面サーバーだけ自動起動
    python3 setup_autostart.py install --hub https://... --token ...    # 常時稼働サーバーへの同期も自動起動
    python3 setup_autostart.py status                                   # 登録・起動の状態を表示
    python3 setup_autostart.py uninstall                                # 登録を解除

~/.claude/settings.json の hooks.SessionStart に autostart.py を呼ぶ設定を1つ追加する。
既存の設定は保持し、書き換える前にバックアップ(settings.json.bak-日時)を作る。
URL とトークンは ~/.claude-code-program/config.json(本人だけが読める権限)に保存する。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUTOSTART = HERE / "autostart.py"
SETTINGS = Path.home() / ".claude" / "settings.json"
APP_DIR = Path.home() / ".claude-code-program"
CONFIG_FILE = APP_DIR / "config.json"
DEFAULT_APP_NAME = "Claude Code 作業履歴"  # manifest の name。アプリとしてインストールしたときの名前


def is_ours(hook: dict) -> bool:
    """このプログラムが登録したフックか(フォルダを移動した後の古い登録も含む)。"""
    args = hook.get("args") or []
    # 旧フォルダ名(claude_code_program)で登録したものも、このプログラムの登録として扱う
    names = ("claude_code_history_viewer", "claude_code_program")
    return any(
        str(a).replace("\\", "/").endswith("/autostart.py") and any(n in str(a).replace("\\", "/") for n in names) for a in args
    ) or (
        bool(args) and Path(str(args[-1])) == AUTOSTART
    )


def load_settings() -> dict:
    if not SETTINGS.exists():
        return {}
    text = SETTINGS.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        sys.exit(f"{SETTINGS} が JSON として読めないため、変更しませんでした: {exc}")
    if not isinstance(data, dict):
        sys.exit(f"{SETTINGS} の形式が想定と違うため、変更しませんでした")
    return data


def save_settings(data: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        backup = SETTINGS.with_name(f"settings.json.bak-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(SETTINGS, backup)
        print(f"バックアップ: {backup}")
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, SETTINGS)


def remove_ours(data: dict) -> int:
    """SessionStart から自分の登録を取り除き、取り除いた数を返す。"""
    groups = (data.get("hooks") or {}).get("SessionStart")
    if not isinstance(groups, list):
        return 0
    removed = 0
    kept_groups = []
    for g in groups:
        hooks = g.get("hooks") if isinstance(g, dict) else None
        if not isinstance(hooks, list):
            kept_groups.append(g)
            continue
        kept = [h for h in hooks if not (isinstance(h, dict) and is_ours(h))]
        removed += len(hooks) - len(kept)
        if kept:
            kept_groups.append(dict(g, hooks=kept))
    if kept_groups:
        data["hooks"]["SessionStart"] = kept_groups
    else:
        del data["hooks"]["SessionStart"]
        if not data["hooks"]:
            del data["hooks"]
    return removed


def write_config(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)  # トークンを含むので本人だけが読めるようにする
    os.replace(tmp, CONFIG_FILE)


def read_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cmd_install(args) -> None:
    """登録する。設定は前回の内容に、今回指定した項目だけを上書きする(--reset で作り直し)。"""
    if bool(args.hub) != bool(args.token):
        sys.exit("--hub と --token は両方指定してください")
    if args.hub and args.hub.startswith("http://") and not args.allow_http:
        sys.exit("http:// は暗号化されません。https:// の URL を指定してください(試験用途なら --allow-http)")

    cfg = {} if args.reset else read_config()
    cfg.setdefault("viewer", True)
    cfg.setdefault("port", 8765)
    if args.viewer is not None:
        cfg["viewer"] = args.viewer
    if args.port is not None:
        cfg["port"] = args.port
    if args.no_hub:
        for k in ("hub", "token", "allow_http", "no_logs"):
            cfg.pop(k, None)
    if args.hub:
        cfg.update(hub=args.hub.rstrip("/"), token=args.token, allow_http=bool(args.allow_http), no_logs=bool(args.no_logs))
    if args.no_ntfy:
        for k in ("ntfy_url", "ntfy_token", "public_url", "ntfy_private"):
            cfg.pop(k, None)
    if args.ntfy_url:
        cfg.update(ntfy_url=args.ntfy_url, ntfy_token=args.ntfy_token or "", public_url=args.public_url or "", ntfy_private=bool(args.ntfy_private))
    if args.open is not None:
        cfg["open"] = args.open
    if args.app_name:
        cfg["app_name"] = args.app_name
    if cfg.get("ntfy_url") and not cfg["viewer"]:
        sys.exit("ntfy の通知は画面サーバーが送るので、画面サーバーを自動起動しない設定とは一緒に使えません")
    if cfg.get("open", "off") != "off" and not cfg["viewer"]:
        sys.exit("画面を自動で開くには、画面サーバーの自動起動が必要です")
    write_config(cfg)

    data = load_settings()
    remove_ours(data)
    hook = {"type": "command", "command": sys.executable, "args": [str(AUTOSTART)], "timeout": 30}
    data.setdefault("hooks", {}).setdefault("SessionStart", []).append({"matcher": "startup|resume", "hooks": [hook]})
    save_settings(data)

    print(f"登録しました: {SETTINGS}")
    print(f"設定: {CONFIG_FILE}")
    print("次に Claude Code を起動したときから有効になります。今すぐ起動するには: " + f'"{sys.executable}" "{AUTOSTART}"')
    if cfg["viewer"]:
        print(f"画面: http://127.0.0.1:{cfg['port']}")
    opened = {"app": f"アプリ「{cfg.get('app_name', DEFAULT_APP_NAME)}」(見つからなければブラウザ)", "browser": "ブラウザ"}.get(cfg.get("open", "off"))
    print(f"自動で開く: {opened + '(画面がどこにも開いていないときだけ)' if opened else 'しない'}")
    if args.ntfy_url or args.port is not None:
        print("※ すでに画面サーバーが動いている場合は、止めてから起動し直すと反映されます")


def cmd_uninstall(args) -> None:
    data = load_settings()
    n = remove_ours(data) if data else 0
    if n:
        save_settings(data)
        print(f"自動起動の登録を解除しました({SETTINGS})")
    else:
        print("登録は見つかりませんでした")
    if args.purge and APP_DIR.exists():
        shutil.rmtree(APP_DIR)
        print(f"設定とログを削除しました: {APP_DIR}")
    print("すでに動いているビューア・同期は、パソコンの再起動か、プロセスの終了で止まります")


def cmd_status(args) -> None:
    sys.path.insert(0, str(HERE))
    from autostart import SYNC_LOCK, load_config, lock_held, port_in_use

    data = load_settings()
    groups = (data.get("hooks") or {}).get("SessionStart") or []
    registered = any(isinstance(h, dict) and is_ours(h) for g in groups if isinstance(g, dict) for h in (g.get("hooks") or []))
    cfg = load_config()
    port = int(cfg.get("port", 8765))
    print(f"自動起動の登録 : {'あり' if registered else 'なし'}({SETTINGS})")
    print(f"画面サーバー   : {'自動起動しない設定' if not cfg.get('viewer', True) else ('起動中' if port_in_use(port) else '停止中')}  http://127.0.0.1:{port}")
    if cfg.get("hub"):
        print(f"同期           : {'起動中' if lock_held(SYNC_LOCK) else '停止中'}  → {cfg['hub']}")
    else:
        print("同期           : 設定なし")
    print(f"ntfy 通知      : {'有効 → ' + cfg['ntfy_url'] if cfg.get('ntfy_url') else '設定なし'}")
    mode = cfg.get("open", "off")
    print(f"自動で開く     : {'しない' if mode == 'off' else ('アプリ「' + cfg.get('app_name', DEFAULT_APP_NAME) + '」' if mode == 'app' else 'ブラウザ')}")
    print(f"ログ           : {APP_DIR / 'logs'}")


def main():
    ap = argparse.ArgumentParser(description="Claude Code 起動時の自動起動を登録・解除する")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install", help="登録する(再実行すると、指定した項目だけ設定を変える)")
    p.add_argument("--reset", action="store_true", help="前回の設定を捨てて作り直す")
    p.add_argument("--hub", help="常時稼働サーバーの URL(同期も自動起動する場合)")
    p.add_argument("--token", help="送信用トークン(PUSH_TOKEN)")
    p.add_argument("--no-hub", action="store_true", help="同期の設定を消す")
    p.add_argument("--port", type=int, default=None, help="画面サーバーのポート(既定 8765)")
    p.add_argument("--no-viewer", dest="viewer", action="store_const", const=False, default=None, help="画面サーバーは自動起動しない(同期だけ)")
    p.add_argument("--viewer", dest="viewer", action="store_const", const=True, help="画面サーバーを自動起動する(既定)")
    p.add_argument("--open", choices=["off", "app", "browser"], default=None,
                   help="Claude Code の起動時、画面がどこにも開いていなければ開く(app: インストールしたアプリ / browser: ブラウザ / off: 開かない)")
    p.add_argument("--app-name", help=f"--open app で開くアプリの名前(既定「{DEFAULT_APP_NAME}」)")
    p.add_argument("--no-logs", action="store_true", help="会話ログを送らない")
    p.add_argument("--allow-http", action="store_true", help="http:// の URL を許可する(試験用)")
    p.add_argument("--ntfy-url", help="ntfy でスマホへ通知する場合のトピック URL(例: https://ntfy.sh/<推測されにくい名前>)")
    p.add_argument("--ntfy-token", help="ntfy のアクセストークン(必要な場合)")
    p.add_argument("--public-url", help="通知をタップしたときに開くビューアの URL")
    p.add_argument("--ntfy-private", action="store_true", help="通知にセッション名・プロジェクト名を入れない")
    p.add_argument("--no-ntfy", action="store_true", help="ntfy 通知の設定を消す")
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("uninstall", help="登録を解除する")
    p.add_argument("--purge", action="store_true", help="設定(トークン)とログも削除する")
    p.set_defaults(func=cmd_uninstall)
    p = sub.add_parser("status", help="状態を表示する")
    p.set_defaults(func=cmd_status)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
