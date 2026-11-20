"""내장 코딩 에이전트(WorkspaceTools + CodingAgent) 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobmate.agent import CodingAgent, WorkspaceTools
from jobmate.config import Settings
from jobmate.llm import FakeLLM


def _ws(tmp_path: Path) -> WorkspaceTools:
    return WorkspaceTools(tmp_path)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "t.db"),
        budget_max_tokens=100_000,
        coding_tool="none",
        coding_timeout_seconds=30.0,
    )


# ---------- WorkspaceTools ----------


def test_ls_read_write(tmp_path: Path):
    t = _ws(tmp_path)
    assert "(empty)" in t.ls()
    assert "wrote" in t.write_file("a/b.txt", "hello\nworld\n")
    assert (tmp_path / "a" / "b.txt").read_text() == "hello\nworld\n"
    out = t.read_file("a/b.txt")
    assert "1: hello" in out and "2: world" in out
    assert "f b.txt" in t.ls("a") or "b.txt" in t.ls("a")


def test_path_escape_blocked(tmp_path: Path):
    t = _ws(tmp_path)
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret")
    # 직접 호출은 ValueError 전파, call() 경유는 에러 문자열 반환
    for fn in (
        lambda: t.read_file("../outside_secret.txt"),
        lambda: t.write_file("../evil.txt", "x"),
        lambda: t.read_file(str(outside)),
        lambda: t.ls("../../"),
    ):
        with pytest.raises(ValueError, match="escapes workspace"):
            fn()
    assert "tool error" in t.call("read_file", {"path": "../outside_secret.txt"})
    assert not (tmp_path.parent / "evil.txt").exists()
    outside.unlink()


def test_edit_file_unique_match(tmp_path: Path):
    t = _ws(tmp_path)
    t.write_file("f.txt", "foo bar foo\n")
    assert "matches 2 times" in t.edit_file("f.txt", "foo", "baz")
    assert "edited" in t.edit_file("f.txt", "bar", "baz")
    assert "baz" in (tmp_path / "f.txt").read_text()
    assert "file not found" in t.edit_file("nope.txt", "a", "b")
    assert "not found" in t.edit_file("f.txt", "zzz", "y")


def test_grep_glob_execute(tmp_path: Path):
    t = _ws(tmp_path)
    t.write_file("src/app.py", "def main():\n    return 42\n")
    hits = t.grep(r"return 42", "src")
    assert "app.py:2" in hits
    assert "invalid regex" in t.grep("([", ".")
    assert "app.py" in t.glob("*.py", "src")
    out = t.execute("echo hi && exit 3")
    assert "exit=3" in out and "hi" in out
    assert "timed out" in t.execute("sleep 5", timeout=1)


def test_call_unknown_and_bad_args(tmp_path: Path):
    t = _ws(tmp_path)
    assert "unknown tool" in t.call("rm_rf", {})
    assert "tool error" in t.call("read_file", {"path": 123})


# ---------- CodingAgent 루프 ----------


def _agent_llm(actions: list[dict]):
    """AgentAction dict 큐를 반환하는 FakeLLM. 큐 소진 후 마지막 반복."""
    queue = list(actions)

    def handler(system, user, model, schema):
        if not queue:
            return {"action": "final", "answer": "fallback done"}
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item

    return FakeLLM(handler)


def test_agent_tool_then_final(tmp_path: Path):
    llm = _agent_llm(
        [
            {
                "action": "tool",
                "thought": "create file",
                "tool": "write_file",
                "args": {"path": "hello.txt", "content": "hi"},
            },
            {"action": "final", "answer": "created hello.txt"},
        ]
    )
    agent = CodingAgent(llm, _settings(tmp_path))
    res = agent.run("make hello.txt", str(tmp_path))
    assert res["status"] == "done"
    assert res["answer"] == "created hello.txt"
    assert (tmp_path / "hello.txt").read_text() == "hi"
    assert len(res["steps"]) == 1
    assert res["steps"][0]["tool"] == "write_file"
    assert "wrote" in res["steps"][0]["result"]
    assert res["usage"]["tokens_in"] > 0


def test_agent_max_steps(tmp_path: Path):
    llm = _agent_llm([{"action": "tool", "tool": "ls", "args": {"path": "."}}])
    agent = CodingAgent(llm, _settings(tmp_path))
    res = agent.run("loop forever", str(tmp_path), max_steps=3)
    assert res["status"] == "max_steps"
    assert res["error"]
    assert len(res["steps"]) == 3


def test_agent_llm_failure(tmp_path: Path):
    class Boom:
        def complete(self, **kw):
            raise RuntimeError("api down")

    agent = CodingAgent(Boom(), _settings(tmp_path))
    res = agent.run("task", str(tmp_path))
    assert res["status"] == "failed"
    assert "llm error" in res["error"]


def test_agent_cancel_check(tmp_path: Path):
    calls = {"n": 0}

    def cancel_check() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    llm = _agent_llm([{"action": "tool", "tool": "ls", "args": {"path": "."}}])
    res = CodingAgent(llm, _settings(tmp_path)).run("t", str(tmp_path), cancel_check=cancel_check)
    assert res["status"] == "cancelled"
    assert "cancelled" in res["error"]


def test_agent_unknown_tool_recorded(tmp_path: Path):
    llm = _agent_llm(
        [
            {"action": "tool", "tool": "nope", "args": {}},
            {"action": "final", "answer": "ok"},
        ]
    )
    res = CodingAgent(llm, _settings(tmp_path)).run("t", str(tmp_path))
    assert res["status"] == "done"
    assert "unknown tool" in res["steps"][0]["result"]
