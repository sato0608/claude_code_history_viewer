"""同期プロセスを1つだけにするためのファイルロック(Mac / Windows / Linux)。

プロセスが終了すると OS がロックを外すので、異常終了してもロックが残らない。
"""

from __future__ import annotations

import os
from pathlib import Path


def try_lock(path: Path):
    """ロックを取れたらファイルハンドルを返す。他のプロセスが持っていれば None。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
