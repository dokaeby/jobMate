"""코딩 CLI 도구 어댑터 — implementer 단계를 실제 코딩 에이전트 CLI로 실행.

지원 도구: claude, codex, antigravity(agy), opencode.
각 어댑터는 헤드리스 모드로 프롬프트를 실행하고, 작업 디렉터리가 git
레포이면 변경분을 unified diff로 수집해 ChangeProposal.diff로 반환한다.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MAX_OUTPUT_CHARS = 60_000
MAX_DIFF_CHARS = 200_000


@dataclass
class CodingToolResult:
    ok: bool
    text: str            # 도구의 최종 텍스트 출력
    diff: str            # 작업 디렉터리의 git 변경분 (unified diff)
    exit_code: int
    tool: str


class CodingTool(Protocol):
    name: str

    def available(self) -> bool:
        """CLI 바이너리가 PATH에 있는지."""
        ...

    def run(self, *, prompt: str, cwd: str, timeout: float) -> CodingToolResult:
        ...


class CodingToolError(Exception):
    pass


def _run_cmd(argv: list[str], cwd: str, timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(  # noqa: S603
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise CodingToolError(f"{argv[0]} not found") from e
    except subprocess.TimeoutExpired as e:
        raise CodingToolError(
            f"{argv[0]} timed out after {timeout}s") from e
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, out[:MAX_OUTPUT_CHARS]


def workspace_diff(cwd: str) -> str:
    """git 레포의 변경분을 unified diff로 수집. 스테이징 없이 untracked 포함."""
    root = Path(cwd)
    if not (root / ".git").exists():
        return ""
    parts: list[str] = []
    code, tracked = _run_cmd(["git", "diff", "HEAD"], cwd, 30)
    if code == 0 and tracked.strip():
        parts.append(tracked[:MAX_DIFF_CHARS])
    # untracked 파일을 new-file diff로 변환
    code, status = _run_cmd(["git", "status", "--porcelain"], cwd, 30)
    if code != 0:
        return "".join(parts)[:MAX_DIFF_CHARS]
    for line in status.splitlines():
        if not line.startswith("??"):
            continue
        f = line[3:].strip().strip('"')
        fp = root / f
        if not fp.is_file():
            continue
        code, d = _run_cmd(
            ["git", "diff", "--no-index", "--", "/dev/null", f], cwd, 30)
        # git diff --no-index는 차이 있을 때 exit 1 — 정상
        if d.strip():
            parts.append(d[:MAX_DIFF_CHARS])
    return "".join(parts)[:MAX_DIFF_CHARS]


class _CliCodingTool:
    """공통 CLI 실행기. argv 템플릿만 서브클래스가 정의."""

    binary = ""
    name = ""

    def argv(self, prompt: str) -> list[str]:  # pragma: no cover - override
        raise NotImplementedError

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def run(self, *, prompt: str, cwd: str, timeout: float) -> CodingToolResult:
        if not self.available():
            raise CodingToolError(f"{self.name}: {self.binary} not on PATH")
        code, out = _run_cmd(self.argv(prompt), cwd, timeout)
        return CodingToolResult(
            ok=code == 0, text=out, diff=workspace_diff(cwd),
            exit_code=code, tool=self.name)


class ClaudeCodingTool(_CliCodingTool):
    """Claude Code 헤드리스: claude -p --output-format text"""

    binary = "claude"
    name = "claude"

    def argv(self, prompt: str) -> list[str]:
        return ["claude", "-p", prompt, "--output-format", "text",
                "--permission-mode", "acceptEdits"]


class CodexCodingTool(_CliCodingTool):
    """Codex CLI 헤드리스: codex exec --sandbox workspace-write"""

    binary = "codex"
    name = "codex"

    def argv(self, prompt: str) -> list[str]:
        return ["codex", "exec", "--sandbox", "workspace-write", prompt]


class AntigravityCodingTool(_CliCodingTool):
    """Antigravity CLI(agy) 헤드리스: agy --print --mode auto"""

    binary = "agy"
    name = "antigravity"

    def argv(self, prompt: str) -> list[str]:
        return ["agy", "--print", "--mode", "auto", prompt]


class OpenCodeCodingTool(_CliCodingTool):
    """OpenCode 헤드리스: opencode run --auto"""

    binary = "opencode"
    name = "opencode"

    def argv(self, prompt: str) -> list[str]:
        return ["opencode", "run", "--auto", prompt]


CODING_TOOLS: dict[str, type[_CliCodingTool]] = {
    "claude": ClaudeCodingTool,
    "codex": CodexCodingTool,
    "antigravity": AntigravityCodingTool,
    "opencode": OpenCodeCodingTool,
}


def build_coding_tool(name: str) -> CodingTool | None:
    cls = CODING_TOOLS.get(name)
    return cls() if cls else None
