"""코딩 도구 어댑터 단위 테스트."""

import os
import subprocess

import pytest

from jobmate.tools.coding import (
    AntigravityCodingTool,
    ClaudeCodingTool,
    CodexCodingTool,
    CodingToolError,
    OpenCodeCodingTool,
    _run_cmd,
    build_coding_tool,
    workspace_diff,
)

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def _git(ws, *args):
    subprocess.run([*GIT, *args], cwd=ws, check=True)  # noqa: S603,S607


def test_argv_shapes():
    assert ClaudeCodingTool().argv("do it")[:2] == ["claude", "-p"]
    assert CodexCodingTool().argv("do it")[:3] == ["codex", "exec", "--sandbox"]
    argv = AntigravityCodingTool().argv("do it")
    assert argv[0] == "agy" and argv[1].startswith("--print=do it")
    assert "--dangerously-skip-permissions" in argv
    assert "--sandbox" in argv
    assert OpenCodeCodingTool().argv("do it")[:3] == ["opencode", "run", "--auto"]


def test_model_passthrough():
    assert "--model" not in " ".join(CodexCodingTool().argv("x"))
    argv = OpenCodeCodingTool(model="opencode/big-pickle").argv("x")
    assert argv[argv.index("--model") + 1] == "opencode/big-pickle"
    agy = AntigravityCodingTool(model="gpt-x").argv("x")
    assert "--model" in agy and agy[agy.index("--model") + 1] == "gpt-x"


def test_run_cmd_sets_pwd(tmp_path):
    code, out = _run_cmd(["/bin/sh", "-c", 'printf %s "$PWD"'], str(tmp_path), 10)
    assert code == 0
    assert out.strip() == os.path.abspath(str(tmp_path))


def test_build_coding_tool_unknown():
    assert build_coding_tool("nope") is None
    assert build_coding_tool("claude") is not None


def test_workspace_diff_git_repo(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)  # noqa: S603,S607
    _git(ws, "commit", "-q", "--allow-empty", "-m", "i")
    (ws / "a.txt").write_text("v1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=ws, check=True)  # noqa: S603,S607
    _git(ws, "commit", "-q", "-m", "a")
    (ws / "a.txt").write_text("v2\n")
    (ws / "new.txt").write_text("brand new\n")
    diff = workspace_diff(str(ws))
    assert "diff --git" in diff
    assert "a.txt" in diff and "new.txt" in diff
    assert "+v2" in diff and "+brand new" in diff


def test_workspace_diff_non_repo(tmp_path):
    assert workspace_diff(str(tmp_path)) == ""


def test_run_missing_binary(tmp_path):
    class NoBin(CodexCodingTool):
        binary = "definitely-not-a-real-cli-xyz"

    with pytest.raises(CodingToolError):
        NoBin().run(prompt="x", cwd=str(tmp_path), timeout=1)


def test_run_cmd_cancel_kills_process(tmp_path):
    """cancel_check가 True면 실행 중인 프로세스를 kill하고 -15를 반환한다."""
    code, out = _run_cmd(["sleep", "30"], str(tmp_path), timeout=60, cancel_check=lambda: True)
    assert code == -15
    assert "cancelled" in out


def test_run_cmd_timeout(tmp_path):
    with pytest.raises(CodingToolError, match="timed out"):
        _run_cmd(["sleep", "30"], str(tmp_path), timeout=1)
