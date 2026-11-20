"""dev_review implement 단계를 코딩 도구 어댑터로 실행하는 통합 테스트."""

from jobmate.models import RunStatus
from jobmate.tools.coding import CodingToolResult
from jobmate.workflows.registry_builder import build_runner

from .helpers import VALID_DIFF, default_outputs, make_env, scripted_llm


class FakeCodingTool:
    name = "fake"

    def __init__(self, result: CodingToolResult):
        self.result = result
        self.calls: list[dict] = []

    def run(self, *, prompt: str, cwd: str, timeout: float) -> CodingToolResult:
        self.calls.append({"prompt": prompt, "cwd": cwd})
        return self.result


def _ok(diff: str = VALID_DIFF) -> CodingToolResult:
    return CodingToolResult(ok=True, text="implemented", diff=diff, exit_code=0, tool="fake")


def test_implement_via_coding_tool():
    settings, store = make_env(coding_tool="codex")
    llm = scripted_llm(default_outputs())
    tool = FakeCodingTool(_ok())
    runner = build_runner("dev_review", store=store, llm=llm, settings=settings, coding=tool)
    run = store.create_run("dev_review", "0.1.0", {"task": "t"}, trace_id="t2")
    runner.invoke(run.id, {"task": "t"})
    impl = store.get_stage_runs(run.id)
    stages = {s.stage_key: s for s in impl}
    assert stages["implement"].status.value == "succeeded"
    out = stages["implement"].output
    assert out["diff"] == VALID_DIFF
    assert tool.calls, "coding tool not invoked"
    assert "# Task" in tool.calls[0]["prompt"]


def test_implement_coding_tool_failure_retries():
    settings, store = make_env(coding_tool="codex")
    llm = scripted_llm(default_outputs())
    bad = CodingToolResult(ok=False, text="boom", diff="", exit_code=1, tool="fake")
    tool = FakeCodingTool(bad)
    runner = build_runner("dev_review", store=store, llm=llm, settings=settings, coding=tool)
    run = store.create_run("dev_review", "0.1.0", {"task": "t"}, trace_id="t3")
    runner.invoke(run.id, {"task": "t"})
    r = store.get_run(run.id)
    # implement max_attempts(3) 초과 → failed 에스컬레이션
    assert r.status in (RunStatus.FAILED, RunStatus.NEEDS_HUMAN, RunStatus.BUDGET_EXHAUSTED)
    assert len(tool.calls) >= 1
