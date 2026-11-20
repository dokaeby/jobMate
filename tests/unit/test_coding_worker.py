"""CodingJobWorker 큐/lease/재시도 + SSE + 샌드박스 테스트."""

from __future__ import annotations

import time
from pathlib import Path

from jobmate.coding_worker import CodingJobWorker
from jobmate.config import Settings
from jobmate.llm import FakeLLM
from jobmate.sandbox import make_wrapper, seatbelt_profile
from jobmate.store.db import Store
from tests.integration.helpers import scripted_llm


def _env(tmp_path: Path):
    db = str(tmp_path / "t.db")
    settings = Settings(
        db_path=db,
        budget_max_tokens=100_000,
        coding_tool="none",
        coding_timeout_seconds=10.0,
    )
    store = Store(db)
    return settings, store


def _wait_job(store: Store, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get_coding_job(job_id)
        assert job is not None
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {store.get_coding_job(job_id)}")


def test_claim_lease_and_orphan(tmp_path: Path):
    _, store = _env(tmp_path)
    store.create_coding_job("j1", engine="agent", tool="agent", model="", task="x")
    # queued → claim 성공, attempts 증가
    assert store.claim_coding_job("j1", "w1", lease_seconds=30)
    job = store.get_coding_job("j1")
    assert job["status"] == "running" and job["attempts"] == 1
    assert job["lease_owner"] == "w1"
    # 다른 owner는 lease 만료 전 claim 불가
    assert not store.claim_coding_job("j1", "w2", lease_seconds=30)
    # lease 갱신
    assert store.renew_coding_lease("j1", "w1", lease_seconds=30)
    assert not store.renew_coding_lease("j1", "w2", lease_seconds=30)
    # 만료된 lease는 orphan으로 회수 가능
    store.update_coding_job("j1", lease_expires_at="2000-01-01T00:00:00+00:00")
    orphans = store.list_orphaned_coding_jobs()
    assert any(j["id"] == "j1" for j in orphans)
    assert store.claim_coding_job("j1", "w2", lease_seconds=30)
    assert store.get_coding_job("j1")["attempts"] == 2


def test_retry_schedule(tmp_path: Path):
    _, store = _env(tmp_path)
    store.create_coding_job("j2", engine="agent", tool="agent", model="", task="x", max_attempts=2)
    store.claim_coding_job("j2", "w1")
    # 1차 실패 → 재큐
    status = store.schedule_coding_retry("j2", delay_seconds=0, error="boom")
    assert status == "queued"
    job = store.get_coding_job("j2")
    assert job["status"] == "queued" and job["next_run_at"]
    # 2차 실패 → 최종 failed
    store.claim_coding_job("j2", "w1")
    status = store.schedule_coding_retry("j2", delay_seconds=0, error="boom2")
    assert status == "failed"
    assert store.get_coding_job("j2")["status"] == "failed"


def test_worker_executes_agent_job(tmp_path: Path):
    settings, store = _env(tmp_path)
    llm = scripted_llm(
        {
            "AgentAction": [
                {
                    "action": "tool",
                    "tool": "write_file",
                    "args": {"path": "w.txt", "content": "worker"},
                },
                {"action": "final", "answer": "ok"},
            ]
        }
    )
    worker = CodingJobWorker(store, settings, llm, owner="test-w")
    store.create_coding_job("jw", engine="agent", tool="agent", model="", task="make w.txt")
    assert worker.submit("jw")
    job = _wait_job(store, "jw")
    assert job["status"] == "done" and job["answer"] == "ok"
    assert job["steps"] and job["steps"][0]["tool"] == "write_file"
    # 스텝이 증분 저장됐는지 — 최종 rows에 workspace/diff 포함
    assert "w.txt" in (job["diff"] or "") or job["workspace"]


def test_worker_cancel_running_job(tmp_path: Path):
    settings, store = _env(tmp_path)

    # final을 절대 안 하는 느린 LLM — 취소가 도착할 시간을 준다
    def slow_handler(**kw):
        time.sleep(0.1)
        return {"action": "tool", "tool": "ls", "args": {}}

    llm = FakeLLM(slow_handler)
    worker = CodingJobWorker(store, settings, llm, owner="test-w")
    store.create_coding_job("jc", engine="agent", tool="agent", model="", task="loop")
    worker.submit("jc")
    time.sleep(0.35)
    assert store.cancel_coding_job("jc") == "cancel_requested"
    job = _wait_job(store, "jc")
    assert job["status"] == "cancelled"


def test_dispatch_once_picks_due_jobs(tmp_path: Path):
    settings, store = _env(tmp_path)
    llm = scripted_llm({"AgentAction": {"action": "final", "answer": "d"}})
    worker = CodingJobWorker(store, settings, llm, owner="disp")
    store.create_coding_job("jd", engine="agent", tool="agent", model="", task="t")
    # submit 없이 dispatch_only로 회수
    assert worker.dispatch_once() == 1
    job = _wait_job(store, "jd")
    assert job["status"] == "done"


# ---------- sandbox ----------


def test_sandbox_none_returns_none(tmp_path: Path):
    assert make_wrapper(str(tmp_path), "none") is None
    assert make_wrapper(str(tmp_path), "bogus") is None


def test_seatbelt_profile_blocks_outside(tmp_path: Path):
    profile = seatbelt_profile(str(tmp_path))
    assert "deny network" in profile and "deny file-write" in profile
    assert str(tmp_path) in profile
    wrap = make_wrapper(str(tmp_path), "seatbelt")
    if wrap is None:
        return  # sandbox-exec 없는 플랫폼
    import subprocess

    out = subprocess.run(  # noqa: S603
        wrap(["/bin/sh", "-c", "touch inside.txt"]),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert out.returncode == 0
    assert (tmp_path / "inside.txt").exists()
    # 홈 디렉터리 쓰기는 차단되어야 함
    out2 = subprocess.run(  # noqa: S603
        wrap(["/bin/sh", "-c", 'touch "$HOME/sb_should_not_exist.txt"']),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert out2.returncode != 0
