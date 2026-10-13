"""動作確認用の合成履歴を作る(実際の作業記録ではない)。

    python tests/make_demo_data.py OUT_DIR            # 過去1週間分のセッションを書き出す
    python tests/make_demo_data.py OUT_DIR --live     # さらに 1 セッションへ数秒ごとに記録を追記し続ける

出力は OUT_DIR/projects/<project>/<sessionId>.jsonl。server.py --claude-dir OUT_DIR で表示できる。
"""

import argparse
import json
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECTS = ["/home/demo/web-app", "/home/demo/api-server", "/home/demo/notes"]
TOPICS = [
    "ログイン画面のバリデーションを直して",
    "APIのレスポンスが遅い原因を調べて",
    "READMEに使い方を追記して",
    "テストが落ちているので直して",
    "CSVの読み込み処理をリファクタリングして",
    "グラフの色を見やすくして",
    "依存パッケージを更新して",
]


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class Writer:
    def __init__(self, root: Path, project: str, sid: str):
        self.sid = sid
        self.cwd = project
        d = root / "projects" / project.replace("/", "-")
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{sid}.jsonl"
        self.n = 0

    def _base(self, ts, rtype):
        return {
            "type": rtype, "uuid": str(uuid.uuid4()), "timestamp": iso(ts), "sessionId": self.sid,
            "cwd": self.cwd, "gitBranch": "main", "version": "demo", "entrypoint": "cli", "isSidechain": False,
        }

    def write(self, rec):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def prompt(self, ts, text):
        r = self._base(ts, "user")
        r["message"] = {"role": "user", "content": text}
        self.write(r)

    def assistant(self, ts, blocks, stop):
        self.n += 1
        r = self._base(ts, "assistant")
        r["message"] = {
            "id": f"msg_{self.sid[:8]}_{self.n}", "role": "assistant", "model": "demo-model", "content": blocks,
            "stop_reason": stop, "usage": {"input_tokens": 5, "output_tokens": random.randint(50, 800),
                                            "cache_read_input_tokens": random.randint(1000, 30000), "cache_creation_input_tokens": 500},
        }
        self.write(r)

    def tool(self, ts, name, inp, result="ok", is_error=False, tool_use_result=None):
        tid = "toolu_" + uuid.uuid4().hex[:12]
        self.assistant(ts, [{"type": "tool_use", "id": tid, "name": name, "input": inp}], "tool_use")
        r = self._base(ts + 2, "user")
        r["message"] = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": is_error}]}
        if tool_use_result is not None:
            r["toolUseResult"] = tool_use_result
        self.write(r)


def one_turn(w: Writer, t: float, kind: str) -> float:
    if kind == "実装":
        f = f"{w.cwd}/src/{random.choice(['app', 'form', 'util', 'chart'])}.py"
        w.tool(t, "Read", {"file_path": f}); t += 20
        w.tool(t, "Edit", {"file_path": f, "old_string": "a", "new_string": "b"}); t += 40
        ok = random.random() > 0.25
        w.tool(t, "Bash", {"command": "pytest -q"}, "1 passed" if ok else "1 failed", is_error=not ok); t += 60
    elif kind == "調査":
        for _ in range(3):
            w.tool(t, random.choice(["Grep", "Read", "Glob"]), {"pattern": "def main"}); t += 25
    elif kind == "実行・検証":
        for _ in range(2):
            w.tool(t, "Bash", {"command": "python run.py"}, "done"); t += 45
    w.assistant(t, [{"type": "text", "text": "対応しました。"}], "end_turn")
    return t + 5


def make_session(root: Path, start: float, rng: random.Random) -> None:
    w = Writer(root, rng.choice(PROJECTS), str(uuid.uuid4()))
    kind = rng.choice(["実装", "実装", "調査", "実行・検証", "会話"])
    t = start
    w.prompt(t, rng.choice(TOPICS)); t += 5
    w.tool(t, "TaskCreate", {"subject": "原因を特定する", "description": "-"}, "Task #1 created successfully", tool_use_result={"task": {"id": "1"}}); t += 5
    w.tool(t, "TaskCreate", {"subject": "修正して確認する", "description": "-"}, "Task #2 created successfully", tool_use_result={"task": {"id": "2"}}); t += 5
    turns = rng.randint(2, 6)
    for i in range(turns):
        t = one_turn(w, t, kind)
        t += rng.randint(3, 12) * 60  # 応答を読んで次の指示を書くまでの時間
        if i == 0:
            w.tool(t, "TaskUpdate", {"taskId": "1", "status": "completed"}, "Updated"); t += 5
        if rng.random() < 0.3:
            t += rng.randint(15, 90) * 60  # 休憩(帯が分かれる)
        w.prompt(t, "続けて"); t += 5
    if kind == "実装" and rng.random() < 0.7:
        h = uuid.uuid4().hex[:7]
        w.tool(t, "Bash", {"command": 'git commit -m "修正を反映"'}, f"[main {h}] 修正を反映\n 1 file changed"); t += 10
        w.tool(t, "TaskUpdate", {"taskId": "2", "status": "completed"}, "Updated")
    if rng.random() < 0.15:
        w.prompt(t + 30, "[Request interrupted by user]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    root = Path(args.out)
    rng = random.Random(args.seed)
    random.seed(args.seed)
    now = time.time()
    for _ in range(40):
        day = rng.randint(0, 9)
        start = now - day * 86400 - rng.randint(0, 86400)
        if start < now - 3600:
            make_session(root, start, rng)
    if args.live:
        w = Writer(root, PROJECTS[0], str(uuid.uuid4()))
        w.prompt(time.time(), "(デモ)リアルタイム更新の確認用セッション")
        while True:
            time.sleep(4)
            w.tool(time.time(), random.choice(["Read", "Bash", "Edit"]), {"file_path": f"{w.cwd}/src/live.py", "command": "ls"})


if __name__ == "__main__":
    main()
