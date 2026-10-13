"""Claude Code の起動時(SessionStart フック)に呼ばれ、ビューアと同期を裏で起動する。

- 画面サーバー(server.py、local モード)が動いていなければ起動する。
- 設定に常時稼働サーバーの URL とトークンがあれば、同期(sync_client.py)が動いていなければ起動する。
- すでに動いていれば何もしない。起動したプロセスは Claude Code を終了しても動き続ける。
- 設定で「自動で開く」を選んでいれば、画面がどこにも開いていないときだけ、アプリ(またはブラウザ)で開く。
- このスクリプトの標準出力は Claude の文脈に入るため、何も出力しない(記録はログファイルへ)。

設定・ログの置き場所: ~/.claude-code-program/(setup_autostart.py が作る)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = Path.home() / ".claude-code-program"
CONFIG_FILE = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
SYNC_LOCK = APP_DIR / "sync.lock"
OPEN_LOCK = APP_DIR / "open.lock"
LAST_OPEN = APP_DIR / "last_open"
# 一度開いてからこの秒数の間は、もう一度は開かない(Claude Code の起動処理が続けて2回呼ばれた場合の重複を防ぐ)
OPEN_GUARD_SEC = 60
LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_APP_NAME = "Claude Code 作業履歴"
# 画面サーバーを起動し直したとき、開いていた画面が再接続してくるまで待つ秒数(ページ側は3秒後に再接続する)
RECONNECT_WAIT_SEC = 5


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def lock_held(path: Path) -> bool:
    """同期プロセスが持つロックファイルが使用中かを調べる。"""
    from sync_lock import try_lock, unlock

    handle = try_lock(path)
    if handle is None:
        return True
    unlock(handle)
    return False


def background_python() -> str:
    """Windows ではコンソール画面を出さない pythonw.exe を使う。"""
    exe = Path(sys.executable)
    if os.name == "nt":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def open_log(name: str):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / name
    mode = "w" if path.exists() and path.stat().st_size > LOG_MAX_BYTES else "a"  # 大きくなりすぎたら作り直す
    fh = open(path, mode, encoding="utf-8")
    fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 起動 =====\n")
    fh.flush()
    return fh


def spawn(args: list, log_name: str, extra_env: dict | None = None) -> None:
    """親(Claude Code のフック)から切り離して起動する。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", **(extra_env or {}))
    log = open_log(log_name)
    kwargs = dict(cwd=str(HERE), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True)
    if os.name == "nt":
        DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP, CREATE_BREAKAWAY_FROM_JOB = 0x8, 0x200, 0x01000000
        try:
            # フックを動かしたプロセスのジョブから外し、Claude Code 終了時に巻き込まれないようにする
            subprocess.Popen(args, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:
            subprocess.Popen(args, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, **kwargs)
    else:
        subprocess.Popen(args, start_new_session=True, **kwargs)
    log.close()


def viewer_count(port: int) -> int | None:
    """開いている画面の数。分からなければ None。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/viewers", timeout=3) as res:
            return int(json.loads(res.read().decode("utf-8")).get("viewers", 0))
    except Exception:
        return None


def windows_browser_exe() -> str | None:
    """Windows で、画面だけの窓(--app)を開ける Chrome / Edge の場所を探す。"""
    env = os.environ
    candidates = []
    for base in (env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("LocalAppData")):
        if base:
            candidates += [Path(base) / "Google/Chrome/Application/chrome.exe", Path(base) / "Microsoft/Edge/Application/msedge.exe"]
    return next((str(p) for p in candidates if p.exists()), None)


def launch_commands(cfg: dict, url: str) -> list:
    """開くためのコマンドの候補(上から順に試す)。最後はブラウザ。"""
    mode = cfg.get("open", "off")
    cmds = []
    if mode == "app":
        if sys.platform == "darwin":
            # インストールしたアプリを名前で開く。無ければ Chrome の「画面だけの窓」
            cmds.append(["open", "-a", cfg.get("app_name", DEFAULT_APP_NAME)])
            cmds.append(["open", "-na", "Google Chrome", "--args", f"--app={url}"])
        elif os.name == "nt":
            exe = windows_browser_exe()
            if exe:
                cmds.append([exe, f"--app={url}"])
    cmds.append(["<browser>", url])
    return cmds


def open_viewer(cfg: dict, fresh: bool) -> None:
    """画面サーバーの起動を待ち、画面がどこにも開いていなければ開く。"""
    from sync_lock import try_lock

    print(f"開く処理を開始(pid={os.getpid()}、サーバー新規起動={'はい' if fresh else 'いいえ'})")
    # ほぼ同時に2回呼ばれても、開く処理は1つだけにする(ロックはこのプロセスが終わるまで持つ)
    lock = try_lock(OPEN_LOCK)
    if lock is None:
        print("別の開く処理が動いているので、こちらは何もしません")
        return
    port = int(cfg.get("port", 8765))
    url = f"http://127.0.0.1:{port}/"
    for _ in range(75):  # 最大15秒、サーバーの起動を待つ
        if port_in_use(port):
            break
        time.sleep(0.2)
    else:
        print("画面サーバーが起動しなかったので、開くのをやめました")
        return
    if fresh:
        time.sleep(RECONNECT_WAIT_SEC)
    n = viewer_count(port)
    if n:
        print(f"画面はすでに {n} 個開いているので、開きません")
        return
    try:
        last = float(LAST_OPEN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = 0.0
    if time.time() - last < OPEN_GUARD_SEC:
        print(f"{time.time() - last:.0f}秒前に開いたばかりなので、開きません")
        return
    LAST_OPEN.write_text(str(time.time()), encoding="utf-8")
    dry = os.environ.get("CCP_OPEN_DRYRUN")  # テスト用: 実際には開かず、試すコマンドを記録する
    for cmd in launch_commands(cfg, url):
        if dry:
            with open(dry, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(cmd, ensure_ascii=False) + "\n")
            return
        if cmd[0] == "<browser>":
            webbrowser.open(cmd[1])
            print(f"ブラウザで開きました: {cmd[1]}")
            return
        try:
            if subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).returncode == 0:
                print(f"開きました: {cmd}")
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        print(f"開けませんでした(次の方法を試します): {cmd}")


def main() -> None:
    cfg = load_config()
    # Claude Code から何回呼ばれたかを後で確かめられるよう、呼ばれた時刻を残す
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "hook.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} フック実行 pid={os.getpid()}\n")
    except OSError:
        pass
    py = background_python()
    port = int(cfg.get("port", 8765))
    server_was_running = port_in_use(port)

    if cfg.get("viewer", True) and not server_was_running:
        args = [py, str(HERE / "server.py"), "--port", str(port)]
        env = {}
        if cfg.get("ntfy_url"):
            args += ["--ntfy-url", cfg["ntfy_url"]]
            if cfg.get("public_url"):
                args += ["--public-url", cfg["public_url"]]
            if cfg.get("ntfy_private"):
                args.append("--ntfy-private")
            if cfg.get("ntfy_token"):
                env["NTFY_TOKEN"] = cfg["ntfy_token"]  # コマンドラインに出さない
        spawn(args, "server.log", env)

    hub, token = cfg.get("hub"), cfg.get("token")
    if hub and token and not lock_held(SYNC_LOCK):
        args = [py, str(HERE / "sync_client.py"), "--hub", hub, "--lock-file", str(SYNC_LOCK)]
        if cfg.get("allow_http"):
            args.append("--allow-http")
        if cfg.get("no_logs"):
            args.append("--no-logs")
        spawn(args, "sync.log", {"PUSH_TOKEN": token})

    if cfg.get("viewer", True) and cfg.get("open", "off") != "off":
        # サーバーの起動待ちに時間がかかることがあるので、別のプロセスで行い、Claude Code を待たせない
        args = [py, str(HERE / "autostart.py"), "--open-viewer"] + ([] if server_was_running else ["--fresh"])
        spawn(args, "open.log")


if __name__ == "__main__":
    if "--open-viewer" in sys.argv:
        try:
            open_viewer(load_config(), "--fresh" in sys.argv)
        except Exception as exc:
            print(f"開く処理でエラー: {exc!r}")
        sys.exit(0)
    try:
        main()
    except Exception as exc:  # フックの失敗で Claude Code の作業を妨げない
        try:
            with open_log("autostart_error.log") as fh:
                fh.write(f"{exc!r}\n")
        except OSError:
            pass
    sys.exit(0)
