"""Claude Code の作業履歴(~/.claude/projects/**/*.jsonl)を読み、セッション単位に集計する。

- 各 jsonl は 1 行 1 レコード。追記されていくので、ファイルごとに読み込み済みの位置を覚えて
  差分だけを解析する(リアルタイム更新用)。
- セッションの稼働状態は ~/.claude/sessions/<pid>.json(status: busy / idle)と、
  そのプロセスが生きているかで判定する。ファイルが無い場合は履歴の末尾から推定する。
- 元の履歴ファイルは読み取るだけで、一切書き換えない。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 連続する記録の間隔がこれを超えたら、別の作業区間(カレンダーの帯)として分ける
SEGMENT_GAP_SEC = 10 * 60
# プロセス情報が無いとき、最後の記録からこの秒数以内で応答途中なら「作業中(推定)」とする
RECENT_ACTIVE_SEC = 3 * 60

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "ToolSearch"}
RUN_TOOLS = {"Bash", "BashOutput", "KillShell", "Monitor"}
DELEGATE_TOOLS = {"Agent", "Task"}

INTERRUPT_MARK = "[Request interrupted by user"
# コマンドとしての git commit(行頭か ; & | の直後)。文字列の中の "git commit" は数えない
COMMIT_CMD_RE = re.compile(r"(?:^|[;&|(]\s*|\n\s*)git\s+(?:-C\s+\S+\s+)?commit\b(?![^\n;&|]*--dry-run)")
COMMIT_MSG_RE = re.compile(r"""git\s+commit\b[^\n]*?-m\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|\$\(cat\s+<<'?EOF'?\n(.*?)\n)""", re.S)
# git commit -F - <<'EOF' ... EOF の形(標準入力からメッセージを渡す)
# ヒアドキュメントの本文(<<EOF から EOF まで)。本文はコマンドとして実行されないので判定から外す
HEREDOC_BODY_RE = re.compile(r"""(<<-?\s*['"]?(\w+)['"]?[^\n]*)\n.*?\n\s*\2(?=\s|$)""", re.S)
COMMIT_MSG_HEREDOC_RE = re.compile(r"""git\s+commit\b[^\n]*?-F\s+-[^\n]*?<<-?\s*['"]?(\w+)['"]?\n(.*?)\n\1\b""", re.S)
COMMIT_HASH_RE = re.compile(r"^\[([^\]\s]+)(?: \(root-commit\))? ([0-9a-f]{7,40})\]", re.M)

MAX_PROMPTS = 200
MAX_ISSUES = 200
MAX_FILE_TIMES = 100
TEXT_LIMIT = 400


def parse_ts(value) -> float | None:
    """ISO8601 文字列をエポック秒に変換する。"""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def clip(text, limit=TEXT_LIMIT) -> str:
    text = "" if text is None else str(text)
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def content_text(content) -> str:
    """message.content(文字列または block の配列)から本文テキストを取り出す。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    parts.append(content_text(block.get("content")))
        return "\n".join(p for p in parts if p)
    return ""


@dataclass
class Session:
    id: str
    project_dir: str = ""
    cwd: str = ""
    git_branch: str = ""
    version: str = ""
    entrypoint: str = ""
    custom_title: str = ""
    summary_title: str = ""
    first_prompt: str = ""
    start: float | None = None
    end: float | None = None
    timestamps: list = field(default_factory=list)
    prompts: list = field(default_factory=list)
    models: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cacheRead": 0, "cacheCreate": 0})
    seen_message_ids: set = field(default_factory=set)
    tools: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    commits: list = field(default_factory=list)
    todos: list = field(default_factory=list)
    tasks: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    pending_tools: dict = field(default_factory=dict)
    assistant_messages: int = 0
    sidechain_records: int = 0
    last_kind: str = ""  # prompt / tool_result / assistant_tool_use / assistant_end / interrupt / api_error
    last_ts: float | None = None
    files_seen: set = field(default_factory=set)

    # ---- 記録 1 件の取り込み ----
    def add_record(self, rec: dict, source_file: str) -> None:
        rtype = rec.get("type")
        if rtype == "summary" and rec.get("summary"):
            self.summary_title = clip(rec["summary"], 120)
            return
        if rtype == "custom-title" and rec.get("customTitle"):
            self.custom_title = clip(rec["customTitle"], 120)
            return
        if rtype not in ("user", "assistant"):
            return

        self.files_seen.add(source_file)
        ts = parse_ts(rec.get("timestamp"))
        if ts is not None:
            self.timestamps.append(ts)
            self.start = ts if self.start is None else min(self.start, ts)
            self.end = ts if self.end is None else max(self.end, ts)
        for key, attr in (("cwd", "cwd"), ("gitBranch", "git_branch"), ("version", "version"), ("entrypoint", "entrypoint")):
            if rec.get(key):
                setattr(self, attr, rec[key])
        sidechain = bool(rec.get("isSidechain")) or "/subagents/" in source_file.replace(os.sep, "/")
        if sidechain:
            self.sidechain_records += 1

        msg = rec.get("message") or {}
        if rtype == "assistant":
            self._add_assistant(rec, msg, ts, sidechain)
        else:
            self._add_user(rec, msg, ts, sidechain)

    def _add_assistant(self, rec, msg, ts, sidechain):
        mid = msg.get("id") or rec.get("uuid")
        first_block_of_message = mid not in self.seen_message_ids
        if first_block_of_message:
            # 同じ message.id が content block ごとに複数行へ分かれて記録されるため、使用量は 1 回だけ数える
            self.seen_message_ids.add(mid)
            self.assistant_messages += 1
            model = msg.get("model")
            if model and model != "<synthetic>":
                self.models[model] = self.models.get(model, 0) + 1
            usage = msg.get("usage") or {}
            self.tokens["input"] += int(usage.get("input_tokens") or 0)
            self.tokens["output"] += int(usage.get("output_tokens") or 0)
            self.tokens["cacheRead"] += int(usage.get("cache_read_input_tokens") or 0)
            self.tokens["cacheCreate"] += int(usage.get("cache_creation_input_tokens") or 0)

        if rec.get("isApiErrorMessage"):
            self._issue(ts, "api_error", "APIエラー", content_text(msg.get("content")))
            if not sidechain:
                self._set_last("api_error", ts)
            return

        has_tool_use = False
        for block in msg.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            has_tool_use = True
            name = block.get("name") or "?"
            inp = block.get("input") or {}
            self.tools[name] = self.tools.get(name, 0) + 1
            self.pending_tools[block.get("id")] = (name, inp)
            if name in EDIT_TOOLS:
                path = inp.get("file_path") or inp.get("notebook_path")
                if path:
                    f = self.files.setdefault(path, {"edits": 0, "last": ts, "times": []})
                    f["edits"] += 1
                    f["last"] = ts
                    if ts is not None:
                        # レポートで「その期間に編集したか」を判定するため、編集時刻を残す(多すぎる分は古い方から捨てる)
                        f["times"].append(round(ts, 1))
                        del f["times"][:-MAX_FILE_TIMES]
            elif name == "TodoWrite" and isinstance(inp.get("todos"), list):
                self.todos = [
                    {"content": clip(t.get("content"), 200), "status": t.get("status", "pending"), "ts": ts}
                    for t in inp["todos"]
                    if isinstance(t, dict)
                ]
            elif name == "TaskUpdate" and inp.get("taskId") is not None:
                task = self.tasks.get(str(inp["taskId"]))
                if task:
                    if inp.get("status"):
                        task["status"] = inp["status"]
                    if inp.get("subject"):
                        task["content"] = clip(inp["subject"], 200)
                    task["ts"] = ts

        if not sidechain:
            stop = msg.get("stop_reason")
            if has_tool_use or stop == "tool_use":
                self._set_last("assistant_tool_use", ts)
            elif stop in ("end_turn", "stop_sequence", "max_tokens") or stop is None:
                self._set_last("assistant_end", ts)

    def _add_user(self, rec, msg, ts, sidechain):
        content = msg.get("content")
        result = rec.get("toolUseResult")
        blocks = content if isinstance(content, list) else []
        tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]

        for block in tool_results:
            name, inp = self.pending_tools.pop(block.get("tool_use_id"), ("?", {}))
            text = content_text(block.get("content"))
            if block.get("is_error"):
                self._issue(ts, "tool_error", name, text, detail=self._tool_brief(name, inp))
            if name == "Bash":
                self._maybe_commit(inp, text, ts, bool(block.get("is_error")))
            elif name == "TaskCreate":
                tid = None
                if isinstance(result, dict) and isinstance(result.get("task"), dict):
                    tid = result["task"].get("id")
                if tid is None:
                    m = re.search(r"Task #(\S+) created", text)
                    tid = m.group(1) if m else str(len(self.tasks) + 1)
                self.tasks[str(tid)] = {
                    "id": str(tid),
                    "content": clip(inp.get("subject") or inp.get("description"), 200),
                    "status": "pending",
                    "ts": ts,
                }

        if tool_results:
            extra = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
            if INTERRUPT_MARK in extra and not sidechain:
                self._issue(ts, "interrupt", "中断", extra)
                self._set_last("interrupt", ts)
            elif not sidechain:
                self._set_last("tool_result", ts)
            return
        if rec.get("isMeta") or sidechain:
            return

        text = content_text(content)
        if INTERRUPT_MARK in text:
            self._issue(ts, "interrupt", "中断", text)
            self._set_last("interrupt", ts)
            return
        if text.startswith("<command-") or text.startswith("<local-command"):
            # スラッシュコマンドのメタ情報は発言として数えない
            m = re.search(r"<command-name>([^<]+)</command-name>", text)
            if not m:
                return
            text = m.group(1)
        if not text.strip():
            if any(isinstance(b, dict) and b.get("type") == "image" for b in blocks):
                text = "(画像)"
            else:
                return
        if not self.first_prompt:
            self.first_prompt = clip(text, 200)
        if len(self.prompts) < MAX_PROMPTS:
            self.prompts.append({"ts": ts, "text": clip(text, 300)})
        self._set_last("prompt", ts)

    def _tool_brief(self, name, inp):
        if name == "Bash":
            return clip(inp.get("command"), 160)
        for key in ("file_path", "notebook_path", "pattern", "url", "query", "description"):
            if inp.get(key):
                return clip(inp[key], 160)
        return ""

    def _maybe_commit(self, inp, result_text, ts, is_error):
        cmd = inp.get("command") or ""
        # エラー(終了コードが0以外)になったものは、コミットが成立していないとみなす
        shell_part = HEREDOC_BODY_RE.sub(r"\1", cmd)
        if is_error or not COMMIT_CMD_RE.search(shell_part):
            return
        # -q で実行すると結果にハッシュが出ないので、その場合はハッシュ不明として数える
        m_hash = COMMIT_HASH_RE.search(result_text or "")
        message = ""
        m_msg = COMMIT_MSG_RE.search(shell_part)
        if m_msg:
            message = next((g for g in m_msg.groups() if g), "")
        else:
            m_doc = COMMIT_MSG_HEREDOC_RE.search(cmd)
            if m_doc:
                message = m_doc.group(2)
        self.commits.append(
            {
                "ts": ts,
                "hash": m_hash.group(2) if m_hash else "",
                "branch": m_hash.group(1) if m_hash else "",
                "message": clip(message.strip().split("\n")[0], 160),
            }
        )

    def _issue(self, ts, kind, label, text, detail=""):
        if len(self.issues) >= MAX_ISSUES:
            self.issues.pop(0)
        self.issues.append({"ts": ts, "kind": kind, "label": label, "text": clip(text, 300), "detail": detail})

    def _set_last(self, kind, ts):
        if ts is None:
            return
        if self.last_ts is None or ts >= self.last_ts:
            self.last_kind = kind
            self.last_ts = ts

    # ---- 集計 ----
    def segments(self):
        """記録の時刻を、間隔 SEGMENT_GAP_SEC 以内で連結した作業区間 [開始, 終了] の列にする。"""
        ts = sorted(self.timestamps)
        if not ts:
            return []
        segs = [[ts[0], ts[0]]]
        for t in ts[1:]:
            if t - segs[-1][1] > SEGMENT_GAP_SEC:
                segs.append([t, t])
            else:
                segs[-1][1] = t
        return segs

    def work_type(self) -> str:
        """ツールの使用回数から作業種別を決める(推定)。"""
        edit = sum(v for k, v in self.tools.items() if k in EDIT_TOOLS)
        read = sum(v for k, v in self.tools.items() if k in READ_TOOLS)
        run = sum(v for k, v in self.tools.items() if k in RUN_TOOLS)
        if edit + read + run == 0:
            return "会話"
        if edit > 0 and edit * 3 >= max(read, run):
            return "実装"
        if run >= read:
            return "実行・検証"
        return "調査"

    def title(self) -> str:
        return self.custom_title or self.summary_title or self.first_prompt or "(無題)"


class HistoryStore:
    """projects ディレクトリを監視し、セッション集計を差分更新する。"""

    def __init__(self, claude_dir: Path, projects_dir: Path | None = None):
        self.claude_dir = Path(claude_dir)
        self.projects_dir = Path(projects_dir) if projects_dir else self.claude_dir / "projects"
        self.sessions: dict[str, Session] = {}
        self.offsets: dict[str, tuple[int, int]] = {}  # path -> (読んだバイト位置, inode)
        self.live: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.version = 0

    # ---- ファイル走査 ----
    def scan(self) -> set[str]:
        """新しく追記された行を取り込み、変化のあったセッション ID の集合を返す。"""
        changed: set[str] = set()
        if not self.projects_dir.exists():
            return changed
        for path in self.projects_dir.rglob("*.jsonl"):
            try:
                st = path.stat()
            except OSError:
                continue
            key = str(path)
            pos, ino = self.offsets.get(key, (0, st.st_ino))
            if ino != st.st_ino or st.st_size < pos:
                pos = 0  # 置き換え・切り詰めがあれば最初から読み直す
            if st.st_size == pos:
                continue
            try:
                with open(path, "rb") as fh:
                    fh.seek(pos)
                    data = fh.read()
            except OSError:
                continue
            last_nl = data.rfind(b"\n")
            if last_nl < 0:
                continue  # 書き込み途中の行しか無い
            chunk = data[: last_nl + 1]
            self.offsets[key] = (pos + len(chunk), st.st_ino)
            project_dir = path.relative_to(self.projects_dir).parts[0]
            with self.lock:
                for line in chunk.splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    sid = rec.get("sessionId") or path.stem
                    sess = self.sessions.get(sid)
                    if sess is None:
                        sess = self.sessions[sid] = Session(id=sid, project_dir=project_dir)
                    sess.add_record(rec, key)
                    changed.add(sid)
        changed |= self._scan_titles()
        if changed:
            self.version += 1
        return changed

    def _scan_titles(self) -> set[str]:
        """<project>/<sessionId>/custom-title.json(ユーザーが付けたセッション名)を読む。"""
        changed = set()
        for sid, sess in self.sessions.items():
            p = self.projects_dir / sess.project_dir / sid / "custom-title.json"
            try:
                title = json.loads(p.read_text(encoding="utf-8")).get("customTitle")
            except (OSError, ValueError, AttributeError):
                continue
            if title and clip(title, 120) != sess.custom_title:
                sess.custom_title = clip(title, 120)
                changed.add(sid)
        return changed

    def scan_live(self) -> dict[str, dict]:
        """~/.claude/sessions/*.json から、起動中プロセスのセッション状態を読む。"""
        live = {}
        sdir = self.claude_dir / "sessions"
        if sdir.exists():
            for p in sdir.glob("*.json"):
                try:
                    info = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                sid, pid = info.get("sessionId"), info.get("pid")
                if not sid or not isinstance(pid, int) or not pid_alive(pid):
                    continue
                live[sid] = {"status": info.get("status"), "name": info.get("name"), "pid": pid}
        self.live = live
        return live

    # ---- 出力 ----
    def status_of(self, sess: Session, now: float) -> tuple[str, str]:
        """(状態, 判定の根拠) を返す。状態は running / waiting / interrupted / error / ended。"""
        info = self.live.get(sess.id)
        if info:
            if info.get("status") == "busy":
                return "running", "process"
            if info.get("status") == "idle":
                return "waiting", "process"
            return "running", "process"
        if sess.last_kind == "interrupt":
            return "interrupted", "transcript"
        if sess.last_kind == "api_error":
            return "error", "transcript"
        if sess.last_ts and now - sess.last_ts < RECENT_ACTIVE_SEC and sess.last_kind in ("prompt", "tool_result", "assistant_tool_use"):
            return "running", "transcript"
        return "ended", "transcript"

    def summary(self, sess: Session, now: float) -> dict:
        status, status_source = self.status_of(sess, now)
        segs = sess.segments()
        active = sum(max(60.0, b - a) for a, b in segs)
        live_name = (self.live.get(sess.id) or {}).get("name")
        tasks = list(sess.tasks.values()) or sess.todos
        return {
            "id": sess.id,
            "title": live_name or sess.title(),
            "firstPrompt": sess.first_prompt,
            "project": sess.cwd or sess.project_dir,
            "projectName": Path(sess.cwd).name if sess.cwd else sess.project_dir,
            "branch": sess.git_branch,
            "version": sess.version,
            "entrypoint": sess.entrypoint,
            "start": sess.start,
            "end": sess.end,
            "activeSec": round(active),
            "segments": [[round(a, 3), round(b, 3)] for a, b in segs],
            "status": status,
            "statusSource": status_source,
            "lastKind": sess.last_kind,
            "workType": sess.work_type(),
            "models": sess.models,
            "tokens": sess.tokens,
            "assistantMessages": sess.assistant_messages,
            "promptCount": len(sess.prompts),
            "prompts": sess.prompts,
            "tools": sess.tools,
            "files": [{"path": k, **v} for k, v in sorted(sess.files.items(), key=lambda kv: -(kv[1]["last"] or 0))],
            "commits": sess.commits,
            "tasks": [dict(t) for t in tasks if t.get("status") != "deleted"],
            "taskSource": "TaskCreate" if sess.tasks else ("TodoWrite" if sess.todos else ""),
            "issues": sess.issues,
            "subagentRecords": sess.sidechain_records,
        }

    def all_summaries(self) -> list[dict]:
        now = time.time()
        with self.lock:
            return [self.summary(s, now) for s in self.sessions.values() if s.start is not None]

    def summaries_for(self, ids) -> list[dict]:
        now = time.time()
        with self.lock:
            return [self.summary(self.sessions[i], now) for i in ids if i in self.sessions and self.sessions[i].start is not None]

    def session_log(self, sid: str, limit: int = 400) -> list[dict]:
        """会話ログ(発言・応答・ツール呼び出し)を、そのセッションのファイルから読み直して返す。"""
        with self.lock:
            sess = self.sessions.get(sid)
            files = sorted(sess.files_seen) if sess else []
        events = []
        for f in files:
            try:
                fh = open(f, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("sessionId") != sid or rec.get("type") not in ("user", "assistant") or rec.get("isMeta"):
                        continue
                    events.extend(_log_events(rec))
        events.sort(key=lambda e: e["ts"] or 0)
        return events[-limit:]


def _log_events(rec):
    ts = parse_ts(rec.get("timestamp"))
    side = bool(rec.get("isSidechain"))
    content = (rec.get("message") or {}).get("content")
    out = []
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    for b in content or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text" and b.get("text", "").strip():
            kind = "prompt" if rec["type"] == "user" else "text"
            out.append({"ts": ts, "kind": kind, "text": clip(b["text"], 2000), "side": side})
        elif t == "tool_use":
            inp = b.get("input") or {}
            brief = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("description") or inp.get("subject") or ""
            out.append({"ts": ts, "kind": "tool_use", "tool": b.get("name"), "text": clip(brief, 500), "side": side})
        elif t == "tool_result":
            out.append(
                {"ts": ts, "kind": "error" if b.get("is_error") else "tool_result", "text": clip(content_text(b.get("content")), 600), "side": side}
            )
    return out


def pid_alive(pid: int) -> bool:
    """プロセスが生きているかを調べる(プロセスには何もしない)。"""
    if os.name == "nt":
        # Windows の os.kill(pid, 0) はプロセスを終了させてしまうので使わず、API で終了コードを問い合わせる
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
