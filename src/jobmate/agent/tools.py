"""jobMate 내장 코딩 에이전트 — open-swe(MDA) 파일/셸 도구의 경량 포팅.

WorkspaceTools: 작업 디렉터리 안에 갇힌 ls/read/write/edit/glob/grep/execute.
모든 경로는 workspace 기준으로 resolve하며 `..`/절대경로 탈출을 차단한다.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_FILE_CHARS = 20_000
MAX_OUTPUT_CHARS = 8_000
MAX_GREP_RESULTS = 50
EXECUTE_TIMEOUT = 120
EXECUTE_TIMEOUT_MAX = 600


class WorkspaceTools:
    """workspace 내 파일/셸 프리미티브. 각 메서드는 str 결과를 반환한다."""

    def __init__(
        self,
        workspace: str | Path,
        argv_wrap: Callable[[list[str]], list[str]] | None = None,
    ) -> None:
        self.root = Path(workspace).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"workspace not found: {workspace}")
        self._argv_wrap = argv_wrap

    def _resolve(self, path: str) -> Path:
        raw = Path(path)
        p = raw.resolve() if raw.is_absolute() else (self.root / path.lstrip("/")).resolve()
        # 절대경로는 workspace 내부여야 함
        try:
            p.relative_to(self.root)
        except ValueError:
            raise ValueError(f"path escapes workspace: {path}") from None
        return p

    def _clip(self, text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
        suffix = f"\n…(truncated {len(text) - limit} chars)" if len(text) > limit else ""
        return text[:limit] + suffix

    def ls(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not p.is_dir():
            return f"not a directory: {path}"
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        return (
            "\n".join(("d " if e.is_dir() else "f ") + e.name for e in entries[:500]) or "(empty)"
        )

    def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        p = self._resolve(path)
        if not p.is_file():
            return f"file not found: {path}"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"read failed: {e}"
        lines = text.splitlines()
        offset = max(0, int(offset))
        limit = min(max(1, int(limit)), 5000)
        chunk = lines[offset : offset + limit]
        numbered = "\n".join(f"{offset + i + 1}: {line}" for i, line in enumerate(chunk))
        out = numbered or "(empty file)"
        if offset + limit < len(lines):
            out += f"\n…({len(lines) - offset - limit} more lines; use offset={offset + limit})"
        return self._clip(out, MAX_FILE_CHARS)

    def write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as e:
            return f"write failed: {e}"
        return f"wrote {p.relative_to(self.root)} ({len(content)} chars)"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        p = self._resolve(path)
        if not p.is_file():
            return f"file not found: {path}"
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            return f"read failed: {e}"
        if not old_string:
            return "old_string must not be empty"
        count = text.count(old_string)
        if count == 0:
            return "old_string not found in file"
        if count > 1:
            return f"old_string matches {count} times; provide a more unique string"
        p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"edited {p.relative_to(self.root)}"

    def glob(self, pattern: str, path: str = ".") -> str:
        base = self._resolve(path)
        if not base.is_dir():
            return f"not a directory: {path}"
        matches = sorted(str(m.relative_to(self.root)) for m in base.glob(pattern))[
            :MAX_GREP_RESULTS
        ]
        return "\n".join(matches) or "(no matches)"

    def grep(self, pattern: str, path: str = ".") -> str:
        base = self._resolve(path)
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"invalid regex: {e}"
        hits: list[str] = []
        skip = {".git", "node_modules", "__pycache__", ".venv"}
        files = (
            [base]
            if base.is_file()
            else (
                f
                for f in base.rglob("*")
                if f.is_file() and not any(part in skip for part in f.parts)
            )
        )
        for f in files:
            if len(hits) >= MAX_GREP_RESULTS:
                break
            try:
                rel = f.relative_to(self.root)
            except ValueError:
                continue
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                for i, line in enumerate(lines, 1):
                    if rx.search(line):
                        hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(hits) >= MAX_GREP_RESULTS:
                            break
            except OSError:
                continue
        return "\n".join(hits) or "(no matches)"

    def execute(self, command: str, timeout: int = EXECUTE_TIMEOUT) -> str:
        timeout = min(max(1, int(timeout)), EXECUTE_TIMEOUT_MAX)
        env = dict(os.environ, PWD=str(self.root))
        argv = ["/bin/sh", "-c", command]
        if self._argv_wrap is not None:
            argv = self._argv_wrap(argv)
        try:
            proc = subprocess.run(  # noqa: S603
                argv,
                cwd=self.root,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"command timed out after {timeout}s"
        except OSError as e:
            return f"execute failed: {e}"
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return self._clip(f"exit={proc.returncode}\n{out or '(no output)'}")

    def call(self, name: str, args: dict[str, Any]) -> str:
        dispatch: dict[str, Callable[..., str]] = {
            "ls": self.ls,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "glob": self.glob,
            "grep": self.grep,
            "execute": self.execute,
        }
        fn = dispatch.get(name)
        if fn is None:
            avail = "ls, read_file, write_file, edit_file, glob, grep, execute"
            return f"unknown tool: {name}. available: {avail}"
        try:
            return fn(**{k: v for k, v in args.items() if v is not None})
        except (TypeError, ValueError) as e:
            return f"tool error: {e}"
