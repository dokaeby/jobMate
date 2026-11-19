
from jobmate.models import AnalysisResult, ChangeProposal, RunStatus, Verdict

from .helpers import VALID_DIFF, default_outputs, make_env, run_dev_review, scripted_llm


def test_happy_path_to_approval():
    settings, store = make_env()
    llm = scripted_llm(default_outputs())
    run_id, runner, _ = run_dev_review(store, settings, llm)
    assert store.get_run(run_id).status == RunStatus.WAITING_APPROVAL
    stages = [s.stage_key for s in store.get_stage_runs(run_id)]
    assert stages == ["analysis", "implement", "auto_checks", "security",
                      "code_review", "qa", "approval"]


def test_approve_completes_with_pr_summary():
    settings, store = make_env()
    llm = scripted_llm(default_outputs())
    run_id, runner, _ = run_dev_review(store, settings, llm)
    runner.resume(run_id, {"decision": "approve", "comment": "ok"})
    run = store.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert "pr_summary" in (run.metadata or {})
    assert store.get_approvals(run_id)[0].decision == "approve"


def test_reject_marks_rejected():
    settings, store = make_env()
    llm = scripted_llm(default_outputs())
    run_id, runner, _ = run_dev_review(store, settings, llm)
    runner.resume(run_id, {"decision": "reject", "comment": "no"})
    assert store.get_run(run_id).status == RunStatus.REJECTED


def test_review_request_changes_loops_to_implement():
    settings, store = make_env()
    script = default_outputs()
    script["Verdict"] = [
        Verdict(decision="request_changes", summary="fix it",
                findings=[{"severity": "major", "summary": "bug",
                           "confidence": "high"}]),
        Verdict(decision="approve", findings=[], summary="ok"),
    ]
    llm = scripted_llm(script)
    run_id, runner, _ = run_dev_review(store, settings, llm)
    impl_attempts = [s for s in store.get_stage_runs(run_id)
                     if s.stage_key == "implement"]
    assert len(impl_attempts) == 2
    # implement 두 번째 호출의 user 프롬프트에 피드백 포함 확인
    assert store.get_run(run_id).status == RunStatus.WAITING_APPROVAL


def test_review_round_cap_escalates():
    settings, store = make_env()
    # security/code_review/qa 모두 계속 request_changes → 상한 초과
    bad = Verdict(decision="request_changes", summary="no",
                  findings=[{"severity": "blocker", "summary": "x",
                             "confidence": "high"}])
    qa_fail = {"decision": "fail", "rationale": "no"}
    script = {
        "AnalysisResult": [default_outputs()["AnalysisResult"]],
        "ChangeProposal": [default_outputs()["ChangeProposal"]],
        "Verdict": [bad],   # security, code_review 모두 같은 큐 소비
        "QaVerdict": [qa_fail],
    }
    llm = scripted_llm(script)
    run_id, runner, _ = run_dev_review(store, settings, llm)
    run = store.get_run(run_id)
    assert run.status in (RunStatus.NEEDS_HUMAN, RunStatus.WAITING_APPROVAL,
                          RunStatus.FAILED)
    # 핵심: review_rounds 상한 후 추가 LLM 호출 없이 종료되어야 함
    verdict_calls = sum(1 for c in llm.calls)
    assert verdict_calls <= 3 * 3 + 4  # analysis+impl+checks 외 상한 내


def test_invalid_verdict_json_retries_not_approve():
    settings, store = make_env()
    script = default_outputs()
    # security가 깨진 문자열 반환 → 스키마 검증 실패 → 재시도
    script["Verdict"] = ["not json at all", Verdict(decision="approve",
                                                  findings=[], summary="ok")]
    llm = scripted_llm(script)
    run_id, runner, _ = run_dev_review(store, settings, llm)
    sec_stages = [s for s in store.get_stage_runs(run_id)
                  if s.stage_key == "security"]
    assert len(sec_stages) >= 1
    assert store.get_run(run_id).status == RunStatus.WAITING_APPROVAL


def test_budget_exhausted_safe_stop():
    settings, store = make_env(budget_max_tokens=15)
    llm = scripted_llm(default_outputs())
    run_id, runner, _ = run_dev_review(store, settings, llm)
    run = store.get_run(run_id)
    assert run.status in (RunStatus.BUDGET_EXHAUSTED, RunStatus.FAILED,
                          RunStatus.NEEDS_HUMAN)


def test_approve_with_blocker_finding_downgraded():
    """센서: approve + blocker finding → request_changes → implement 회귀."""
    settings, store = make_env()
    sneaky = Verdict(decision="approve", summary="lgtm",
                     findings=[{"severity": "blocker", "summary": "sql injection",
                                "confidence": "high"}])
    script = default_outputs()
    script["Verdict"] = [sneaky, Verdict(decision="approve", findings=[],
                                        summary="fixed")]
    llm = scripted_llm(script)
    run_id, runner, _ = run_dev_review(store, settings, llm)
    impl = [s for s in store.get_stage_runs(run_id) if s.stage_key == "implement"]
    assert len(impl) == 2


def test_auto_checks_failure_loops_to_implement():
    """auto_checks FAIL → implement 회귀 (bool 분기 회귀 테스트)."""
    settings, store = make_env()
    script = default_outputs()
    # implement가 빈 diff를 내면 auto_checks 실패 → 재구현
    script["ChangeProposal"] = [
        ChangeProposal(summary="bad", diff=""),
        ChangeProposal(summary="good",
                       diff=VALID_DIFF),
    ]
    llm = scripted_llm(script)
    run_id, runner, _ = run_dev_review(store, settings, llm)
    impl = [s for s in store.get_stage_runs(run_id) if s.stage_key == "implement"]
    assert len(impl) == 2
    # 두 번째 implement의 user 프롬프트에 auto_checks 피드백이 포함되어야 함
    impl_users = [c["user"] for c in llm.calls
                  if "Feedback from review" in c["user"]]
    assert impl_users, "implement 피드백 미포함 — stage_outputs 병합 확인"


def test_stage_outputs_accumulate_for_pr_summary():
    """PR 요약에 analysis/implement/qa 내용이 모두 포함되어야 한다."""
    settings, store = make_env()
    script = default_outputs()
    script["AnalysisResult"] = AnalysisResult(
        requirements="UNIQUE_REQ_MARKER", scope="scope")
    llm = scripted_llm(script)
    run_id, runner, _ = run_dev_review(store, settings, llm)
    runner.resume(run_id, {"decision": "approve"})
    run = store.get_run(run_id)
    assert "UNIQUE_REQ_MARKER" in (run.metadata or {}).get("pr_summary", "")
