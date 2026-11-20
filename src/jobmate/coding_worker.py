"""코딩 job 워커 — 큐 소비, lease heartbeat, 재시도, 샌드박스 실행.

- submit(): API가 job을 queued로 등록하면 즉시 claim 후 실행 스레드 시작.
- start()/dispatch_once(): 백그라운드 디스패처가 due queued job과
  lease 만료(orphan) running job을 주기적으로 회수한다.
- 실행 중 lease를 heartbeat로 갱신하고, 종료 시 결과를 coding_jobs에
  기록한다. 실패 시 attempts<max_attempts면 지수 백오프로 재큐.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .agent import CodingAgent
from .config import Settings
from .sandbox import make_wrapper
from .store.db import Store
from .tools.coding import CodingToolError, build_coding_tool, workspace_diff

LEASE_SECONDS = 30
RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 300


class CodingJobWorker:
    """coding_jobs 테이블 기반 분산 워커(단일 프로세스 다중 스레드 안전)."""

    def __init__(
        self,
        store: Store,
        settings: Settings,
        llm: Any,
        *,
        owner: str | None = None,
        lease_seconds: int = LEASE_SECONDS,
        poll_interval: float = 1.0,
    ) -> None:
        self._store = store
        self._settings = settings
        self._llm = llm
        self._owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._lease_seconds = lease_seconds
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._dispatch_thread: threading.Thread | None = None
        self._active: dict[str, threading.Thread] = {}
        self._active_lock = threading.Lock()
        self._lease_lost: set[str] = set()
        self._lease_lock = threading.Lock()

    # ---- lifecycle ----
    def start(self) -> None:
        if self._dispatch_thread is not None:
            return
        self._stop.clear()
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, name="coding-dispatcher", daemon=True
        )
        self._dispatch_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._dispatch_thread is not None:
            self._dispatch_thread.join(timeout=5)
            self._dispatch_thread = None

    @property
    def owner(self) -> str:
        return self._owner

    # ---- submit / dispatch ----
    def submit(self, job_id: str) -> bool:
        """job을 즉시 선점해 실행. 이미 실행 중이거나 미래 재시도면 False."""
        if not self._claim(job_id):
            return False
        self._spawn(job_id)
        return True

    def dispatch_once(self) -> int:
        """due queued + lease 만료 orphan job을 회수. 처리한 job 수 반환."""
        n = 0
        for job in self._store.list_due_coding_jobs(limit=10):
            if self._claim(job["id"]):
                self._spawn(job["id"])
                n += 1
        for job in self._store.list_orphaned_coding_jobs(limit=10):
            if self._claim(job["id"]):
                self._spawn(job["id"])
                n += 1
        return n

    def _dispatch_loop(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self.dispatch_once()
            except Exception:
                # 디스패처 루프는 죽지 않는다 — 다음 주기에 재시도
                import logging

                logging.getLogger("jobmate.coding_worker").exception("dispatch_once failed")

    def _claim(self, job_id: str) -> bool:
        return self._store.claim_coding_job(job_id, self._owner, self._lease_seconds)

    def _spawn(self, job_id: str) -> None:
        t = threading.Thread(
            target=self._execute_guarded, args=(job_id,), name=f"coding-job-{job_id}", daemon=True
        )
        with self._active_lock:
            self._active[job_id] = t
        t.start()

    def _execute_guarded(self, job_id: str) -> None:
        stop_hb = threading.Event()
        hb = threading.Thread(
            target=self._heartbeat, args=(job_id, stop_hb), name=f"coding-hb-{job_id}", daemon=True
        )
        hb.start()
        try:
            self._run_job(job_id)
        finally:
            stop_hb.set()
            hb.join(timeout=5)
            with self._active_lock:
                self._active.pop(job_id, None)
            with self._lease_lock:
                self._lease_lost.discard(job_id)

    def _heartbeat(self, job_id: str, stop: threading.Event) -> None:
        interval = max(1.0, self._lease_seconds / 3)
        while not stop.wait(interval):
            try:
                if not self._store.renew_coding_lease(job_id, self._owner, self._lease_seconds):
                    with self._lease_lock:
                        self._lease_lost.add(job_id)
                    return  # lease 상실 — 다른 워커가 회수했거나 job 종료
            except Exception:
                return

    def _lost(self, job_id: str) -> bool:
        with self._lease_lock:
            return job_id in self._lease_lost

    def _lease_lost_log(self, job_id: str) -> None:
        import logging

        logging.getLogger("jobmate.coding_worker").warning(
            "coding job %s: lease lost — fenced write skipped", job_id
        )

    # ---- execution ----
    def _workspace(self, job_id: str, custom: str) -> str:
        if custom:
            p = Path(custom).expanduser()
            if not p.is_dir():
                raise CodingToolError(f"workspace not found: {custom}")
            return str(p)
        if self._settings.coding_workspace:
            return self._settings.coding_workspace
        ws = Path(self._settings.db_path).parent / "workspaces" / f"coding-{job_id}"
        if not (ws / ".git").exists():
            ws.mkdir(parents=True, exist_ok=True)
            subprocess.run(  # noqa: S603
                ["git", "init", "-q"],  # noqa: S607
                cwd=ws,
                check=True,
                timeout=30,
            )
            subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "git",
                    "-c",
                    "user.email=jobmate@local",
                    "-c",
                    "user.name=jobmate",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    "init",
                ],
                cwd=ws,
                check=True,
                timeout=30,
            )
        return str(ws)

    def _sandbox_wrap(self, cwd: str) -> Callable[[list[str]], list[str]] | None:
        return make_wrapper(
            cwd,
            self._settings.coding_sandbox,
            self._settings.coding_sandbox_image,
        )

    def _run_job(self, job_id: str) -> None:
        job = self._store.get_coding_job(job_id)
        if job is None or job.get("status") != "running":
            return
        cancel_check = lambda: (  # noqa: E731
            self._store.coding_job_cancel_requested(job_id) or self._lost(job_id)
        )
        engine = str(job.get("engine") or "")
        try:
            cwd = self._workspace(job_id, str(job.get("workspace") or ""))
            if engine == "agent":
                self._run_agent(job, cwd, cancel_check)
            else:
                self._run_cli(job, cwd, cancel_check)
        except Exception as e:
            self._finish_failure(job_id, str(e)[:500], cancelled=cancel_check())

    def _run_agent(self, job: dict[str, Any], cwd: str, cancel_check: Callable[[], bool]) -> None:
        job_id = str(job["id"])

        def on_step(steps: list[dict[str, Any]]) -> None:
            self._store.update_coding_job(job_id, owner=self._owner, steps=steps)

        res = CodingAgent(self._llm, self._settings).run(
            str(job.get("task") or ""),
            cwd,
            model=str(job.get("model") or ""),
            timeout=self._settings.coding_timeout_seconds,
            cancel_check=cancel_check,
            on_step=on_step,
        )
        status = (
            "done"
            if res["status"] == "done"
            else ("cancelled" if res["status"] == "cancelled" else "failed")
        )
        fields: dict[str, Any] = {
            "ok": status == "done",
            "text": res["answer"],
            "answer": res["answer"],
            "steps": res["steps"],
            "diff": workspace_diff(cwd),
            "workspace": cwd,
            "error": res["error"] or ("" if status == "done" else res["status"]),
        }
        if status == "failed":
            # 부분 결과만 lease 보유 상태로 기록 — 상태 전이는 retry가 담당
            if not self._store.update_coding_job(job_id, owner=self._owner, **fields):
                self._lease_lost_log(job_id)
                return
        else:
            fields["status"] = status
            fields["lease_owner"] = ""
            fields["lease_expires_at"] = None
            if not self._store.update_coding_job(job_id, owner=self._owner, **fields):
                self._lease_lost_log(job_id)
                return
        self._store.audit(
            "",
            "tool:agent",
            "coding_run",
            job_id,
            {"status": res["status"], "steps": len(res["steps"])},
        )
        if status == "failed":
            self._retry_or_fail(job_id, res["error"] or res["status"])

    def _run_cli(self, job: dict[str, Any], cwd: str, cancel_check: Callable[[], bool]) -> None:
        job_id = str(job["id"])
        tool_name = str(job.get("tool") or "")
        tool = build_coding_tool(tool_name, model=str(job.get("model") or "") or None)
        if tool is None:
            raise CodingToolError(f"unknown coding tool: {tool_name}")
        if not tool.available():
            raise CodingToolError(f"{tool_name} CLI not found on PATH")
        res = tool.run(
            prompt=str(job.get("task") or ""),
            cwd=cwd,
            timeout=self._settings.coding_timeout_seconds,
            cancel_check=cancel_check,
            argv_wrap=self._sandbox_wrap(cwd),
        )
        cancelled = res.exit_code == -15 or cancel_check()
        status = "cancelled" if cancelled else ("done" if res.ok else "failed")
        fields: dict[str, Any] = {
            "ok": not cancelled and res.ok,
            "text": res.text,
            "diff": res.diff,
            "workspace": cwd,
            "exit_code": res.exit_code,
            "error": "cancelled by user" if cancelled else ("" if res.ok else res.text[-300:]),
        }
        if status == "failed":
            if not self._store.update_coding_job(job_id, owner=self._owner, **fields):
                self._lease_lost_log(job_id)
                return
        else:
            fields["status"] = status
            fields["lease_owner"] = ""
            fields["lease_expires_at"] = None
            if not self._store.update_coding_job(job_id, owner=self._owner, **fields):
                self._lease_lost_log(job_id)
                return
        self._store.audit(
            "",
            f"tool:{res.tool}",
            "coding_run",
            job_id,
            {"exit_code": res.exit_code, "diff_chars": len(res.diff)},
        )
        if status == "failed":
            self._retry_or_fail(job_id, f"exit={res.exit_code}")

    def _finish_failure(self, job_id: str, error: str, *, cancelled: bool) -> None:
        if cancelled:
            wrote = self._store.update_coding_job(
                job_id,
                owner=self._owner,
                status="cancelled",
                error="cancelled by user",
                lease_owner="",
                lease_expires_at=None,
            )
            if not wrote:
                self._lease_lost_log(job_id)
            return
        self._retry_or_fail(job_id, error)

    def _retry_or_fail(self, job_id: str, error: str) -> None:
        job = self._store.get_coding_job(job_id) or {}
        attempts = int(job.get("attempts") or 1)
        delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)))
        final = self._store.schedule_coding_retry(
            job_id, delay_seconds=delay, error=error, owner=self._owner
        )
        if final == "lost":
            self._lease_lost_log(job_id)
            return
        import logging

        logging.getLogger("jobmate.coding_worker").warning(
            "coding job %s attempt %s failed → %s (next in %ss): %s",
            job_id,
            attempts,
            final,
            delay if final == "queued" else 0,
            error[:120],
        )
