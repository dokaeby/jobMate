"""내장 코딩 에이전트 루프 — open-swe의 ReAct 루프를 JSON 액션 프로토콜로 경량화.

각 스텝: 전체 트랜스크립트를 LLM에 보내고 {"action":"tool"|"final"} JSON을 받는다.
도구 실행 결과는 다음 프롬프트에 포함된다. max_steps/timeout으로 상한을 둔다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..config import Settings
from .tools import WorkspaceTools

MAX_STEPS = 40
RESULT_PREVIEW = 500

SYSTEM = """You are a coding agent working in a workspace directory.
You must respond with EXACTLY ONE JSON object, no other text.

To call a tool:
{"action":"tool","thought":"<brief reasoning>","tool":"<name>","args":{<args>}}

To finish:
{"action":"final","answer":"<summary of what you did and the result>"}

Available tools (all paths relative to workspace root):
- ls(path=".") — list directory entries
- read_file(path, offset=0, limit=2000) — read file with line numbers
- write_file(path, content) — create/overwrite file
- edit_file(path, old_string, new_string) — replace unique exact match
- glob(pattern, path=".") — filename glob
- grep(pattern, path=".") — regex search, returns file:line: text
- execute(command, timeout=120) — run shell command in workspace (max 600s)

Rules:
- Inspect before editing; verify changes (run tests/build) before finishing.
- Keep edits minimal and correct. Do not ask questions — act autonomously.
- If a tool result shows an error, adjust and retry.
- When done, respond with action "final" and a concise Korean summary.
"""


class AgentAction(BaseModel):
    action: Literal["tool", "final"]
    thought: str = ""
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    answer: str = ""


class CodingAgent:
    """LLM + WorkspaceTools 루프. run()은 동기 실행(백그라운드 스레드에서 호출)."""

    def __init__(self, llm: Any, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    def run(
        self,
        task: str,
        workspace: str,
        *,
        model: str = "",
        max_steps: int = MAX_STEPS,
        timeout: float | None = None,
        cancel_check: Callable[[], bool] | None = None,
        on_step: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> dict[str, Any]:
        from ..sandbox import make_wrapper

        wrap = make_wrapper(
            str(Path(workspace).resolve()),
            getattr(self._settings, "coding_sandbox", "none"),
            getattr(self._settings, "coding_sandbox_image", "python:3.12-slim"),
        )
        tools = WorkspaceTools(workspace, argv_wrap=wrap)
        cfg = self._settings.model_for("coding_agent", "important")
        if model:
            cfg = cfg.model_copy(update={"model": model})
        budget = timeout or self._settings.coding_timeout_seconds
        deadline = time.monotonic() + budget
        steps: list[dict[str, Any]] = []
        usage = {"tokens_in": 0, "tokens_out": 0}
        status, answer, error = "done", "", ""

        for i in range(max_steps):
            if cancel_check is not None and cancel_check():
                status, error = "cancelled", "cancelled by user"
                break
            if time.monotonic() > deadline:
                status, error = "timeout", f"agent timeout after {budget}s"
                break
            remaining = max(1, int(deadline - time.monotonic()))
            user = self._prompt(task, steps, remaining)
            try:
                out, u = self._llm.complete(model=cfg, system=SYSTEM, user=user, schema=AgentAction)
            except Exception as e:
                status, error = "failed", f"llm error: {str(e)[:300]}"
                break
            usage["tokens_in"] += int(u.get("tokens_in", 0))
            usage["tokens_out"] += int(u.get("tokens_out", 0))
            act = out if isinstance(out, AgentAction) else AgentAction.model_validate(out)
            if act.action == "final":
                answer = act.answer
                break
            result = tools.call(act.tool, act.args or {})
            steps.append(
                {
                    "step": i + 1,
                    "thought": act.thought[:200],
                    "tool": act.tool,
                    "args": act.args,
                    "result": result[:RESULT_PREVIEW],
                }
            )
            if on_step is not None:
                on_step(list(steps))
        else:
            status, error = "max_steps", f"reached max_steps={max_steps}"

        return {
            "status": status,
            "answer": answer,
            "error": error,
            "steps": steps,
            "usage": usage,
            "workspace": str(tools.root),
        }

    @staticmethod
    def _prompt(task: str, steps: list[dict[str, Any]], remaining: int) -> str:
        lines = [f"TASK:\n{task}\n", f"Time remaining: ~{remaining}s"]
        if steps:
            lines.append("\nSTEPS SO FAR:")
            for s in steps:
                lines.append(
                    f"{s['step']}. {s['tool']}({s['args']})\n"
                    f"   thought: {s['thought']}\n"
                    f"   result: {s['result']}"
                )
        else:
            lines.append("\nNo steps yet — start by inspecting the workspace.")
        return "\n".join(lines)
