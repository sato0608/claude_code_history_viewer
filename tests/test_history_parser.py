"""history_parser の単体テスト(python -m unittest discover tests)。"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_parser import HistoryStore  # noqa: E402


def rec(sid, rtype, ts, message=None, **extra):
    r = {"type": rtype, "sessionId": sid, "timestamp": ts, "cwd": "/w/proj", "gitBranch": "main", "uuid": f"u{ts}{rtype}"}
    if message is not None:
        r["message"] = message
    r.update(extra)
    return r


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdir = self.root / "projects" / "-w-proj"
        self.pdir.mkdir(parents=True)
        self.file = self.pdir / "s1.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, *records, partial=""):
        with open(self.file, "a", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.write(partial)

    def test_usage_counted_once_per_message_and_tools(self):
        usage = {"input_tokens": 1, "output_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 5}
        m1 = {"id": "m1", "role": "assistant", "model": "x", "usage": usage, "stop_reason": "tool_use",
              "content": [{"type": "thinking", "thinking": ""}]}
        m1b = dict(m1, content=[{"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "/w/proj/a.py"}}])
        self.append(
            rec("s1", "user", "2026-10-01T10:00:00Z", {"role": "user", "content": "直して"}),
            rec("s1", "assistant", "2026-10-01T10:00:05Z", m1),
            rec("s1", "assistant", "2026-10-01T10:00:06Z", m1b),
            rec("s1", "user", "2026-10-01T10:00:07Z", {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "失敗", "is_error": True}]}),
        )
        st = HistoryStore(self.root)
        self.assertEqual(st.scan(), {"s1"})
        s = st.all_summaries()[0]
        self.assertEqual(s["tokens"]["output"], 10)  # 同じ message.id は 1 回だけ
        self.assertEqual(s["tools"], {"Edit": 1})
        self.assertEqual(s["files"][0]["path"], "/w/proj/a.py")
        self.assertEqual(len(s["files"][0]["times"]), 1)  # レポート用の編集時刻
        self.assertEqual(s["issues"][0]["kind"], "tool_error")
        self.assertEqual(s["title"], "直して")
        self.assertEqual(s["workType"], "実装")

    def test_incremental_scan_ignores_partial_line(self):
        self.append(rec("s1", "user", "2026-10-01T10:00:00Z", {"role": "user", "content": "一つ目"}), partial='{"type": "us')
        st = HistoryStore(self.root)
        st.scan()
        self.assertEqual(st.all_summaries()[0]["promptCount"], 1)
        self.assertEqual(st.scan(), set())  # 変化なし
        with open(self.file, "a", encoding="utf-8") as fh:  # 書きかけの行を完成させる
            fh.write('er", "sessionId": "s1", "timestamp": "2026-10-01T10:30:00Z", "message": {"role": "user", "content": "二つ目"}}\n')
        self.assertEqual(st.scan(), {"s1"})
        s = st.all_summaries()[0]
        self.assertEqual(s["promptCount"], 2)
        self.assertEqual(len(s["segments"]), 2)  # 30 分空いたので区間が分かれる

    def test_tasks_commits_and_interrupt(self):
        tu = lambda tid, name, inp: {"id": f"m{tid}", "role": "assistant", "stop_reason": "tool_use",
                                     "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}
        res = lambda tid, text: {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}
        self.append(
            rec("s1", "user", "2026-10-01T10:00:00Z", {"role": "user", "content": "作業"}),
            rec("s1", "assistant", "2026-10-01T10:00:01Z", tu("a", "TaskCreate", {"subject": "調べる", "description": "-"})),
            rec("s1", "user", "2026-10-01T10:00:02Z", res("a", "Task #1 created successfully: 調べる"), toolUseResult={"task": {"id": "1"}}),
            rec("s1", "assistant", "2026-10-01T10:00:03Z", tu("b", "TaskUpdate", {"taskId": "1", "status": "completed"})),
            rec("s1", "user", "2026-10-01T10:00:04Z", res("b", "Updated task #1 status")),
            rec("s1", "assistant", "2026-10-01T10:00:05Z", tu("c", "Bash", {"command": 'git commit -m "修正"'})),
            rec("s1", "user", "2026-10-01T10:00:06Z", res("c", "[main 1a2b3c4] 修正\n 1 file changed")),
            rec("s1", "user", "2026-10-01T10:00:07Z", {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}),
        )
        st = HistoryStore(self.root)
        st.scan()
        s = st.all_summaries()[0]
        self.assertEqual(s["tasks"], [{"id": "1", "content": "調べる", "status": "completed", "ts": s["tasks"][0]["ts"]}])
        self.assertEqual(s["commits"][0]["hash"], "1a2b3c4")
        self.assertEqual(s["commits"][0]["message"], "修正")
        self.assertEqual(s["status"], "interrupted")

    def test_live_status_from_sessions_dir(self):
        t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.append(rec("s1", "user", t, {"role": "user", "content": "やって"}))
        (self.root / "sessions").mkdir()
        import os
        (self.root / "sessions" / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "s1", "status": "idle", "name": "名前"}))
        st = HistoryStore(self.root)
        st.scan()
        st.scan_live()
        s = st.all_summaries()[0]
        self.assertEqual((s["status"], s["statusSource"], s["title"]), ("waiting", "process", "名前"))


class CommitDetectTest(unittest.TestCase):
    def detect(self, cmd, out="", is_error=False):
        from history_parser import Session

        s = Session(id="x")
        s._maybe_commit({"command": cmd}, out, 1.0, is_error)
        return s.commits

    def test_variants(self):
        c = self.detect('git commit -m "修正"', "[main 1a2b3c4] 修正\n 1 file changed")
        self.assertEqual((c[0]["hash"], c[0]["message"]), ("1a2b3c4", "修正"))
        # -q で結果にハッシュが出なくても、成功していれば数える(メッセージは heredoc から取る)
        c = self.detect("cd repo && git add x && git commit -q -F - <<'EOF'\n件名の行\n\n本文\nEOF\ngit push")
        self.assertEqual((c[0]["hash"], c[0]["message"]), ("", "件名の行"))
        self.assertEqual(self.detect('git commit -m "x"', "nothing to commit", is_error=True), [])
        self.assertEqual(self.detect('echo "git commit のやり方"'), [])
        self.assertEqual(self.detect("git commit --dry-run -m x"), [])
        # ヒアドキュメントの本文に書かれた git commit は実行されないので数えない
        self.assertEqual(self.detect("python3 - <<'PY'\nrun('a && git commit -m \"x\"')\nPY"), [])


if __name__ == "__main__":
    unittest.main()
