"""FastAPI 제공층 — 임시. 상용 단계 Django 전환 예정.

도메인 엔진(jobmate.engine/workflows)은 HTTP 프레임워크에 의존하지 않는다.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..adapters import LoggingNotifier, WebPushNotifier
from ..config import (
    Settings,
    configure_langsmith,
    configure_logging,
    database_dsn,
    get_settings,
)
from ..llm import CompositeLLM
from ..models import RunStatus
from ..radar import GitHubAPI
from ..sensors import (
    allowed_identifier,
    build_gmail_url,
    is_safe_source_url,
    verify_slack_signature,
    verify_telegram_secret,
)
from ..store.db import Store
from ..workflows import WORKFLOWS
from ..workflows.registry_builder import build_runner

STATIC_DIR = Path(__file__).parent / "static"


class RunCreateRequest(BaseModel):
    workflow: str = "dev_review"
    task: str = Field(min_length=1, max_length=20_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    comment: str = ""


def _default_adapters(settings: Settings, store: Store) -> dict[str, Any]:
    from ..adapters.google import GoogleCalendar, GoogleGmail

    adapters: dict[str, Any] = {
        "notifier": LoggingNotifier(),
        "github": GitHubAPI(token=os.environ.get(settings.github_token_env, "")),
        "webpush": WebPushNotifier(
            store,
            private_key=os.environ.get("VAPID_PRIVATE_KEY", ""),
            subject=os.environ.get("VAPID_SUBJECT", "mailto:admin@localhost"),
        ),
    }
    # 실제 Google 연동 — 토큰 파일 경로가 설정된 경우에만 주입
    if settings.gmail_token_path:
        adapters["gmail"] = GoogleGmail(settings.gmail_token_path)
    if settings.calendar_token_path:
        adapters["calendar"] = GoogleCalendar(settings.calendar_token_path)
    return adapters


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    llm: Any | None = None,
    adapters: dict[str, Any] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    configure_langsmith(settings)
    store = store or Store(database_dsn(settings))
    llm = llm or CompositeLLM()
    adapters = adapters if adapters is not None else _default_adapters(settings, store)
    stop_event = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store = store
        app.state.settings = settings
        app.state.llm = llm
        app.state.adapters = adapters
        worker = None
        if not os.environ.get("VERCEL"):
            # 서버리스(Vercel)에서는 상주 스레드가 없으므로 cron tick 엔드포인트 사용
            worker = threading.Thread(
                target=_automation_loop,
                args=(store, settings, llm, adapters, stop_event),
                daemon=True,
            )
            worker.start()
        yield
        stop_event.set()
        store.close()

    app = FastAPI(title="jobMate Harness", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def security_middleware(request: Request, call_next: Any) -> Any:
        from ..security import authorized

        path = request.url.path
        # API 키 인증: /api/* 전체. health 체크와 외부 webhook(자체 서명 검증)은 제외
        if path.startswith("/api/") and not path.startswith(
            ("/api/v1/health", "/api/v1/webhooks/")
        ):
            if not authorized(request):
                return err(401, "unauthorized", "invalid or missing API key", request)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def err(status: int, code: str, message: str, request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request.headers.get("x-request-id", uuid.uuid4().hex[:12]),
                }
            },
        )

    def run_detail(run_id: str) -> dict[str, Any] | None:
        run = store.get_run(run_id)
        if run is None:
            return None
        return {
            "id": run.id,
            "workflow": run.workflow_key,
            "version": run.workflow_version,
            "status": run.status.value,
            "trace_id": run.trace_id,
            "error": run.error,
            "input": run.input,
            "metadata": run.metadata,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
            "stages": [s.model_dump(mode="json") for s in store.get_stage_runs(run_id)],
            "approvals": [a.model_dump(mode="json") for a in store.get_approvals(run_id)],
            "audit": [a.model_dump(mode="json") for a in store.get_audit(run_id)],
        }

    def execute_run(
        run_id: str, workflow: str, payload: dict[str, Any], ctx: dict[str, Any] | None = None
    ) -> None:
        try:
            runner = build_runner(
                workflow, store=store, llm=llm, settings=settings, adapters=adapters
            )
            runner.invoke(run_id, payload, gmail_ctx=ctx)
        except Exception as e:
            store.update_run(run_id, status=RunStatus.FAILED, error=str(e)[:500])
            store.audit(run_id, "system", "runner_error", "", {"error": str(e)[:300]})

    def submit_run(workflow: str, task: str, ctx: dict[str, Any] | None = None) -> str:
        run = store.create_run(
            workflow, WORKFLOWS[workflow].version, {"task": task}, trace_id=uuid.uuid4().hex
        )
        threading.Thread(
            target=execute_run, args=(run.id, workflow, {"task": task}, ctx), daemon=True
        ).start()
        return run.id

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        # 루트 스코프로 서빙해야 push 수신 후 openWindow 등이 동작한다
        return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/workflows")
    def list_workflows() -> list[dict[str, str]]:
        return [{"key": w.key, "version": w.version} for w in WORKFLOWS.values()]

    @app.post("/api/v1/runs", status_code=202)
    def create_run(body: RunCreateRequest, request: Request) -> JSONResponse:
        if body.workflow not in WORKFLOWS:
            return err(404, "unknown_workflow", f"workflow '{body.workflow}' not found", request)
        run = store.create_run(
            body.workflow,
            WORKFLOWS[body.workflow].version,
            {"task": body.task, **({"meta": body.metadata} if body.metadata else {})},
            trace_id=uuid.uuid4().hex,
        )
        threading.Thread(
            target=execute_run, args=(run.id, body.workflow, {"task": body.task}, None), daemon=True
        ).start()
        return JSONResponse(status_code=202, content={"id": run.id, "status": run.status.value})

    @app.get("/api/v1/runs")
    def list_runs(limit: int = 50) -> list[dict[str, Any]]:
        return [
            {
                "id": r.id,
                "workflow": r.workflow_key,
                "status": r.status.value,
                "created_at": r.created_at.isoformat(),
            }
            for r in store.list_runs(limit)
        ]

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str, request: Request) -> JSONResponse:
        d = run_detail(run_id)
        if d is None:
            return err(404, "not_found", "run not found", request)
        return JSONResponse(content=d)

    @app.post("/api/v1/runs/{run_id}/approve")
    def approve(run_id: str, body: ApprovalRequest, request: Request) -> JSONResponse:
        run = store.get_run(run_id)
        if run is None:
            return err(404, "not_found", "run not found", request)
        if run.status != RunStatus.WAITING_APPROVAL:
            return err(
                409,
                "invalid_state",
                f"run status is {run.status.value}, not waiting_approval",
                request,
            )
        runner = build_runner(
            run.workflow_key, store=store, llm=llm, settings=settings, adapters=adapters
        )
        try:
            runner.resume(run_id, {"decision": body.decision, "comment": body.comment})
        except Exception as e:
            return err(500, "resume_failed", str(e)[:300], request)
        d = run_detail(run_id)
        return JSONResponse(content=d or {})

    @app.get("/api/v1/notifications")
    def notifications(limit: int = 50) -> list[dict[str, Any]]:
        return [n.model_dump(mode="json") for n in store.list_notifications(limit)]

    @app.get("/api/v1/event-links")
    def event_links(limit: int = 50) -> list[dict[str, Any]]:
        return [x.model_dump(mode="json") for x in store.list_links(limit)]

    @app.get("/api/v1/gmail-calendar")
    def gmail_status() -> dict[str, Any]:
        auto = store.get_automation()
        return {
            "enabled": auto.get("enabled", False),
            "last_poll_at": auto.get("last_poll_at"),
            "last_poll_result": auto.get("last_poll_result", ""),
        }

    @app.post("/api/v1/gmail-calendar/toggle")
    def gmail_toggle(body: dict[str, Any]) -> dict[str, Any]:
        auto = store.get_automation()
        auto["enabled"] = bool(body.get("enabled", not auto.get("enabled", False)))
        store.set_automation(auto)
        return {"enabled": auto["enabled"]}

    @app.post("/api/v1/gmail-calendar/poll")
    def gmail_poll(request: Request) -> JSONResponse:
        gmail = adapters.get("gmail")
        if gmail is None:
            return JSONResponse(
                status_code=202,
                content={"status": "accepted", "note": "poll requires configured gmail adapter"},
            )
        ingested = _poll_gmail(store, settings, gmail)
        return JSONResponse(status_code=202, content={"status": "accepted", "ingested": ingested})

    # ---- calendar-agent: 멀티채널 웹훅·수신함·이벤트·알림예약 ----
    @app.post("/api/v1/webhooks/slack")
    async def slack_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        if not verify_slack_signature(
            body,
            request.headers.get("X-Slack-Request-Timestamp", ""),
            request.headers.get("X-Slack-Signature", ""),
            settings.slack_signing_secret,
        ):
            return err(401, "invalid_signature", "bad slack signature", request)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return err(400, "bad_json", "invalid JSON", request)
        if payload.get("type") == "url_verification":
            return JSONResponse(content={"challenge": payload.get("challenge", "")})
        event = payload.get("event") or {}
        if (
            payload.get("type") != "event_callback"
            or event.get("type") != "message"
            or event.get("bot_id")
            or event.get("subtype") not in (None, "file_share")
            or not (event.get("text") or "").strip()
        ):
            return JSONResponse(content={"status": "ignored"})
        channel = event.get("channel", "")
        if not allowed_identifier(channel, settings.slack_channel_ids):
            return JSONResponse(content={"status": "ignored"})
        team = payload.get("team_id", "")
        ts = event.get("ts", "")
        source_id = f"{team}:{channel}:{ts}"
        text = event.get("text", "")
        created = store.save_message_extraction(
            "slack", source_id, event.get("thread_ts") or ts, {}, "pending", source_text=text[:8000]
        )
        return JSONResponse(
            content={"status": "processed" if created else "duplicate", "source_id": source_id}
        )

    @app.post("/api/v1/webhooks/telegram")
    async def telegram_webhook(request: Request) -> JSONResponse:
        if not verify_telegram_secret(
            request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
            settings.telegram_webhook_secret,
        ):
            return err(401, "invalid_secret", "bad telegram secret", request)
        try:
            update = json.loads(await request.body())
        except json.JSONDecodeError:
            return err(400, "bad_json", "invalid JSON", request)
        msg = update.get("message") or update.get("channel_post") or {}
        sender = msg.get("from") or {}
        if sender.get("is_bot"):
            return JSONResponse(content={"status": "ignored"})
        text = msg.get("text") or msg.get("caption") or ""
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if not text.strip() or not allowed_identifier(chat_id, settings.telegram_chat_ids):
            return JSONResponse(content={"status": "ignored"})
        source_id = f"{chat_id}:{msg.get('message_id', '')}"
        created = store.save_message_extraction(
            "telegram",
            source_id,
            str(msg.get("message_thread_id") or msg.get("message_id", "")),
            {},
            "pending",
            source_text=text[:8000],
        )
        return JSONResponse(
            content={"status": "processed" if created else "duplicate", "source_id": source_id}
        )

    @app.get("/api/v1/messages")
    def list_messages(provider: str | None = None, limit: int = 20) -> dict[str, Any]:
        return {"messages": store.list_message_extractions(provider, limit)}

    @app.post("/api/v1/gmail/digest")
    def gmail_digest(body: dict[str, Any] | None = None) -> JSONResponse:
        """최근 Gmail 메시지들을 LLM으로 요약. 실패 시 빈 summary + error."""
        limit = int((body or {}).get("limit", 20))
        msgs = [
            m
            for m in store.list_message_extractions("gmail", limit)
            if (m.get("source_text") or "").strip()
        ]
        if not msgs:
            return JSONResponse(content={"summary": "", "count": 0})
        lines = []
        for m in msgs[:15]:
            text = (m.get("source_text") or "").replace("\n", " ")[:300]
            lines.append(f"- [{m.get('processing_status')}] {text}")
        system = (
            "너는 이메일 비서다. 다음 Gmail 메시지 목록을 한국어로 "
            "3~5개 bullet로 요약해. 각 bullet은 '- '로 시작하고, "
            "일정/행동 필요한 내용 중심으로 작성해."
        )
        try:
            model = settings.model_for("_digest", "default")
            out, _usage = llm.complete(model=model, system=system, user="\n".join(lines))
            summary = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        except Exception as e:
            return JSONResponse(content={"summary": "", "count": len(msgs), "error": str(e)[:200]})
        return JSONResponse(content={"summary": summary, "count": len(msgs)})

    @app.api_route("/api/v1/cron/tick", methods=["GET", "POST"])
    def cron_tick(request: Request) -> JSONResponse:
        """서버리스 환경용 1회 자동화 사이클 (Vercel Cron 등에서 호출)."""
        secret = os.environ.get("CRON_SECRET") or os.environ.get("JOBMATE_CRON_SECRET", "")
        if os.environ.get("VERCEL") and secret:
            if request.headers.get("authorization") != f"Bearer {secret}":
                return err(401, "unauthorized", "invalid cron secret", request)
        _process_pending_messages(store, settings, llm, adapters)
        _process_due_notification_jobs(store, adapters)
        _maybe_poll_gmail(store, settings, adapters)
        _maybe_daily_radar(store, settings, llm, adapters)
        return JSONResponse(content={"ok": True})

    def _cron_authorized(request: Request) -> bool:
        secret = os.environ.get("CRON_SECRET") or os.environ.get("JOBMATE_CRON_SECRET", "")
        if not secret:
            return True
        return (
            request.headers.get("authorization") == f"Bearer {secret}"
            or request.query_params.get("secret") == secret
        )

    def _summarize_email(msg: dict[str, Any]) -> str:
        body = (msg.get("body_text") or "").strip()
        fallback = "\n".join(ln for ln in body.splitlines() if ln.strip())[:300]
        try:
            model = settings.model_for("_digest", "default")
            out, _u = llm.complete(
                model=model,
                system=(
                    "너는 이메일 비서다. 주어진 이메일을 정확히 3줄로 "
                    "요약해. 각 줄은 '- '로 시작하지 말고 간결한 문장으로."
                ),
                user=(
                    f"제목: {msg.get('subject', '')}\n"
                    f"보낸사람: {msg.get('from_addr', '')}\n"
                    f"본문:\n{body[:3000] or '(본문 없음)'}"
                ),
            )
            if isinstance(out, str):
                lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
                if lines:
                    return "\n".join(lines[:3])
        except Exception as e:
            import logging

            logging.getLogger("jobmate.api").warning("email summarize failed: %s", e)
        return fallback

    @app.post("/api/v1/email/check")
    def email_check(request: Request, body: dict[str, Any] | None = None) -> JSONResponse:
        """Gmail 최근 메일을 읽어 3줄 요약 카드를 생성·저장.
        cron-job.org 등 외부 스케줄러가 30분마다 POST로 호출."""
        if not _cron_authorized(request):
            return err(401, "unauthorized", "invalid cron secret", request)
        gmail = adapters.get("gmail")
        if gmail is None:
            return err(503, "no_gmail_adapter", "gmail adapter not configured", request)
        payload = body or {}
        limit = min(int(payload.get("limit", 5)), 20)
        query = str(payload.get("query", "in:inbox"))
        cards: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            listing = gmail.list_messages(query=query, max_results=limit)
        except Exception as e:
            return JSONResponse(
                status_code=502, content={"ok": False, "error": str(e)[:300], "cards": []}
            )
        for m in listing:
            mid = m["id"]
            try:
                msg = gmail.get_message(mid)
            except Exception as e:
                errors.append(f"{mid}: {str(e)[:120]}")
                continue
            summary = _summarize_email(msg)
            ts = msg.get("internal_date", "")
            message_at = ""
            if ts.isdigit():
                message_at = datetime.fromtimestamp(int(ts) / 1000, UTC).isoformat()
            store.save_email_summary(
                "gmail",
                mid,
                subject=msg.get("subject", ""),
                sender=msg.get("from_addr", ""),
                summary=summary,
                message_at=message_at,
            )
            cards.append(
                {
                    "source_id": mid,
                    "subject": msg.get("subject", ""),
                    "sender": msg.get("from_addr", ""),
                    "summary": summary,
                    "message_at": message_at,
                    "url": f"https://mail.google.com/mail/u/0/#all/{mid}",
                }
            )
        return JSONResponse(
            content={"ok": True, "checked": len(cards), "cards": cards, "errors": errors}
        )

    @app.get("/api/v1/email/summaries")
    def email_summaries(limit: int = 30) -> dict[str, Any]:
        cards = store.list_email_summaries("gmail", min(limit, 100))
        for c in cards:
            c["url"] = f"https://mail.google.com/mail/u/0/#all/{c['source_id']}"
        return {"cards": cards}

    # ---------- settings (대시보드 설정) ----------
    PREFS_KEY = "prefs"

    def _prefs() -> dict[str, Any]:
        return store.get_state(PREFS_KEY, {})

    @app.get("/api/v1/settings")
    def get_prefs() -> dict[str, Any]:
        p = _prefs()
        return {
            "gmail_check_minutes": p.get("gmail_check_minutes", 30),
            "radar_interval": p.get("radar_interval", "daily"),
            "coding_tool": p.get("coding_tool") or settings.coding_tool or "none",
            "coding_model": p.get("coding_model") or settings.coding_model,
        }

    @app.post("/api/v1/settings")
    def set_prefs(body: dict[str, Any], request: Request) -> JSONResponse:
        from ..tools.coding import CODING_TOOLS

        p = _prefs()
        minutes = int(body.get("gmail_check_minutes", p.get("gmail_check_minutes", 30)))
        if minutes not in (30, 60):
            return err(422, "validation", "gmail_check_minutes must be 30 or 60", request)
        interval = str(body.get("radar_interval", p.get("radar_interval", "daily")))
        if interval not in ("daily", "weekly"):
            return err(422, "validation", "radar_interval must be daily or weekly", request)
        tool = str(body.get("coding_tool", p.get("coding_tool", "")))
        if tool and tool != "none" and tool not in CODING_TOOLS:
            return err(422, "validation", f"unknown coding tool: {tool}", request)
        p.update(
            {
                "gmail_check_minutes": minutes,
                "radar_interval": interval,
                "coding_tool": tool or "none",
                "coding_model": str(body.get("coding_model", p.get("coding_model", "")))[:100],
            }
        )
        store.set_state(PREFS_KEY, p)
        cronjob = _sync_cronjob(settings, p)
        return JSONResponse(content={"ok": True, "prefs": p, "cronjob": cronjob})

    # ---------- coding tool (코드 어댑터) ----------
    coding_jobs: dict[str, dict[str, Any]] = {}
    coding_jobs_lock = threading.Lock()

    def _coding_workspace(job_id: str, custom: str = "") -> str:
        """작업 디렉터리. custom이 있으면 그대로, 없으면 run별 git repo 생성."""
        from ..tools.coding import CodingToolError

        if custom:
            p = Path(custom).expanduser()
            if not p.is_dir():
                raise CodingToolError(f"workspace not found: {custom}")
            return str(p)
        if settings.coding_workspace:
            return settings.coding_workspace
        ws = Path(settings.db_path).parent / "workspaces" / f"coding-{job_id}"
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

    @app.get("/api/v1/coding/tools")
    def coding_tools() -> dict[str, Any]:
        from ..tools.coding import CODING_TOOLS, build_coding_tool

        default_tool = _prefs().get("coding_tool") or settings.coding_tool or "none"
        tools = []
        for n in CODING_TOOLS:
            t = build_coding_tool(n)
            tools.append(
                {
                    "name": n,
                    "available": bool(t and t.available()),
                    "default": n == default_tool,
                }
            )
        return {
            "tools": tools,
            "default_model": _prefs().get("coding_model") or settings.coding_model,
        }

    @app.post("/api/v1/coding/run")
    def coding_run(body: dict[str, Any], request: Request) -> JSONResponse:
        from ..tools.coding import build_coding_tool

        engine = str(body.get("engine") or "").strip().lower()
        pref_tool = _prefs().get("coding_tool")
        tool_name = str(body.get("tool") or pref_tool or settings.coding_tool)
        task = str(body.get("task") or "").strip()
        if not task:
            return err(422, "validation", "task is required", request)
        if engine == "agent" or tool_name == "agent":
            engine, tool_name = "agent", "agent"
            tool = None
        else:
            engine = "cli"
            tool = build_coding_tool(
                tool_name, model=str(body.get("model") or settings.coding_model or "") or None
            )
            if tool is None:
                return err(400, "unknown_tool", f"unknown coding tool: {tool_name}", request)
            if not tool.available():
                return err(503, "tool_unavailable", f"{tool_name} CLI not found on PATH", request)
        job_id = uuid.uuid4().hex[:12]
        with coding_jobs_lock:
            coding_jobs[job_id] = {
                "job_id": job_id,
                "status": "running",
                "engine": engine,
                "tool": tool_name,
                "model": body.get("model") or settings.coding_model or "",
                "task": task[:500],
                "created_at": datetime.now(UTC).isoformat(),
                "workspace": "",
                "ok": None,
                "text": "",
                "answer": "",
                "steps": [],
                "diff": "",
                "error": "",
            }

        def worker() -> None:
            try:
                cwd = _coding_workspace(job_id, str(body.get("workspace") or ""))
                if engine == "agent":
                    from ..agent import CodingAgent
                    from ..tools.coding import workspace_diff

                    agent_res = CodingAgent(llm, settings).run(
                        task,
                        cwd,
                        model=str(body.get("model") or ""),
                        timeout=settings.coding_timeout_seconds,
                    )
                    ok = agent_res["status"] == "done"
                    with coding_jobs_lock:
                        coding_jobs[job_id].update(
                            status="done" if ok else "failed",
                            ok=ok,
                            text=agent_res["answer"],
                            answer=agent_res["answer"],
                            steps=agent_res["steps"],
                            diff=workspace_diff(cwd),
                            workspace=cwd,
                            error=agent_res["error"] or ("" if ok else agent_res["status"]),
                        )
                    store.audit(
                        "",
                        "tool:agent",
                        "coding_run",
                        job_id,
                        {"status": agent_res["status"], "steps": len(agent_res["steps"])},
                    )
                    return
                assert tool is not None
                res = tool.run(prompt=task, cwd=cwd, timeout=settings.coding_timeout_seconds)
                with coding_jobs_lock:
                    coding_jobs[job_id].update(
                        status="done",
                        ok=res.ok,
                        text=res.text,
                        diff=res.diff,
                        workspace=cwd,
                        exit_code=res.exit_code,
                    )
                store.audit(
                    "",
                    f"tool:{res.tool}",
                    "coding_run",
                    job_id,
                    {"exit_code": res.exit_code, "diff_chars": len(res.diff)},
                )
            except Exception as e:
                with coding_jobs_lock:
                    coding_jobs[job_id].update(status="failed", error=str(e)[:500])

        threading.Thread(target=worker, daemon=True).start()
        return JSONResponse(status_code=202, content={"job_id": job_id})

    @app.get("/api/v1/coding/jobs/{job_id}")
    def coding_job(job_id: str, request: Request) -> JSONResponse:
        with coding_jobs_lock:
            job = coding_jobs.get(job_id)
        if job is None:
            return err(404, "not_found", "coding job not found", request)
        return JSONResponse(content=dict(job))

    @app.post("/api/v1/messages/{provider}/{source_id}/retry")
    def retry_message(provider: str, source_id: str, request: Request) -> JSONResponse:
        if provider not in ("gmail", "slack", "telegram"):
            return err(400, "bad_provider", "unknown provider", request)
        if not store.has_message(provider, source_id):
            return err(404, "not_found", "message not found", request)
        store.finish_message(provider, source_id, "pending")
        return JSONResponse(content={"status": "pending"})

    @app.get("/api/v1/listeners/status")
    def listeners_status() -> dict[str, Any]:
        auto = store.get_automation()
        return {
            "auto_register": settings.auto_register,
            "gmail_enabled": bool(adapters.get("gmail")),
            "gmail_last_error": auto.get("last_gmail_error", ""),
            "slack_configured": bool(settings.slack_signing_secret),
            "telegram_configured": bool(settings.telegram_webhook_secret),
        }

    @app.get("/api/v1/events")
    def list_events(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return store.list_scheduled_events(status, limit)

    @app.get("/api/v1/notification-jobs")
    def notification_jobs(limit: int = 50) -> list[dict[str, Any]]:
        return store.list_notification_jobs(limit)

    # ---- skillRadar ----
    @app.get("/api/v1/radar/trending")
    def radar_trending(
        platform: str = "claude", date: str | None = None, lang: str = "ko"
    ) -> JSONResponse:
        date_str = date or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        snap = store.radar_snapshot(platform, date_str)
        if snap is None:
            # 최신 스냅샷으로 폴백
            dates = store.radar_dates(platform)
            if not dates:
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": {"code": "no_snapshot", "message": "no snapshot for platform"}
                    },
                )
            date_str = dates[0]
            snap = store.radar_snapshot(platform, date_str)
        if snap is None:
            return JSONResponse(content={"items": [], "date": date_str})
        items = store.radar_items(int(snap["id"]))
        lang_key = {"ko": "summary_ko", "en": "summary_en", "zh": "summary_zh"}.get(
            lang, "summary_ko"
        )
        return JSONResponse(
            content={
                "platform": platform,
                "date": date_str,
                "lang": lang,
                "items": [
                    {
                        "rank": i["rank"],
                        "repo": i["repo_full_name"],
                        "url": i["repo_url"],
                        "description": i["description"],
                        "stars": i["stars"],
                        "stars_today": i["stars_today"],
                        "language": i["language"],
                        "license": i["license"],
                        "taxonomy": i["taxonomy"],
                        "summary": i.get(lang_key) or i.get("summary_ko", ""),
                    }
                    for i in items
                ],
            }
        )

    @app.get("/api/v1/radar/dates")
    def radar_dates(platform: str = "claude") -> dict[str, Any]:
        return {"platform": platform, "dates": store.radar_dates(platform)}

    @app.post("/api/v1/radar/refresh", status_code=202)
    def radar_refresh(body: dict[str, Any] | None = None) -> JSONResponse:
        body = body or {}
        platforms = body.get("platforms") or settings.radar_platforms
        force = bool(body.get("force", True))
        ids = []
        for p in platforms:
            rid = submit_run("skill_radar", f"radar collect {p}", {"platform": p, "force": force})
            ids.append(rid)
        return JSONResponse(status_code=202, content={"run_ids": ids, "platforms": platforms})

    # ---- Web Push 구독 ----
    @app.get("/api/v1/push/vapid-key")
    def vapid_key() -> dict[str, str]:
        return {"public_key": os.environ.get("VAPID_PUBLIC_KEY", "")}

    @app.post("/api/v1/push/subscribe")
    def push_subscribe(body: dict[str, Any]) -> dict[str, str]:
        endpoint = body.get("endpoint", "")
        keys = body.get("keys") or {}
        if endpoint:
            store.save_push_subscription(endpoint, keys)
        return {"status": "subscribed"}

    @app.post("/api/v1/push/unsubscribe")
    def push_unsubscribe(body: dict[str, Any]) -> dict[str, str]:
        store.delete_push_subscription(body.get("endpoint", ""))
        return {"status": "unsubscribed"}

    return app


# ---------- automation worker ----------


def _sync_cronjob(settings: Settings, prefs: dict[str, Any]) -> dict[str, str]:
    """prefs를 cron-job.org 실제 스케줄에 반영. 미설정/실패는 상태 문자열로 반환."""
    import os

    from ..adapters.cronjob import CronJobOrgClient, daily_at, interval_schedule, weekly_at

    api_key = os.environ.get(settings.cronjob_api_key_env, "")
    out: dict[str, str] = {}

    def push(job_id: str, schedule: dict[str, Any], label: str) -> None:
        if not job_id:
            out[label] = "skipped(no job id)"
        elif not api_key:
            out[label] = "skipped(no api key)"
        else:
            try:
                CronJobOrgClient(api_key, job_id, settings.cronjob_base_url).update_schedule(
                    schedule
                )
                out[label] = "updated"
            except Exception as e:
                out[label] = f"error: {str(e)[:120]}"

    push(
        settings.cronjob_job_id,
        interval_schedule(int(prefs.get("gmail_check_minutes", 30))),
        "email",
    )
    radar_schedule = (
        weekly_at(6, 0, wday=1) if prefs.get("radar_interval") == "weekly" else daily_at(6, 0)
    )
    push(settings.cronjob_radar_job_id, radar_schedule, "radar")
    return out


def _automation_loop(
    store: Store, settings: Settings, llm: Any, adapters: dict[str, Any], stop: threading.Event
) -> None:
    """calendar-agent worker 포팅: pending 메시지 처리 + 알림 예약 발송 +
    gmail 폴링 + radar 일일 수집."""
    while not stop.is_set():
        try:
            _process_pending_messages(store, settings, llm, adapters)
            _process_due_notification_jobs(store, adapters)
            _maybe_poll_gmail(store, settings, adapters)
            _maybe_daily_radar(store, settings, llm, adapters)
        except Exception:
            import logging

            logging.getLogger("jobmate.worker").exception("automation loop error")
        stop.wait(settings.worker_interval_seconds)


def _process_pending_messages(
    store: Store, settings: Settings, llm: Any, adapters: dict[str, Any]
) -> None:
    for row in store.pending_messages():
        provider = row["source_provider"]
        source_id = row["source_id"]
        attempts = int(row.get("attempts", 0)) + 1
        try:
            if attempts > 5:
                store.finish_message(
                    provider, source_id, "failed", "max attempts", attempts=attempts
                )
                continue
            ctx = {
                "source_provider": provider,
                "source_id": source_id,
                "thread_id": row.get("thread_id", ""),
                "message_text": row.get("source_text", ""),
            }
            if provider == "gmail":
                ctx["gmail_message_id"] = source_id
            run_id = _create_and_run(
                store, settings, llm, adapters, "gmail_calendar", "message intake", ctx
            )
            store.finish_message(
                provider, source_id, "processed", f"run:{run_id}", attempts=attempts
            )
        except Exception as e:
            retry_at = datetime.now(UTC) + timedelta(seconds=min(300, 2**attempts))
            store.finish_message(
                provider,
                source_id,
                "pending",
                str(e)[:200],
                retry_at=retry_at.isoformat(),
                attempts=attempts,
            )


def _create_and_run(
    store: Store,
    settings: Settings,
    llm: Any,
    adapters: dict[str, Any],
    workflow: str,
    task: str,
    ctx: dict[str, Any] | None = None,
) -> str:
    run = store.create_run(
        workflow, WORKFLOWS[workflow].version, {"task": task}, trace_id=uuid.uuid4().hex
    )
    runner = build_runner(workflow, store=store, llm=llm, settings=settings, adapters=adapters)
    runner.invoke(run.id, {"task": task}, gmail_ctx=ctx)
    return run.id


def _process_due_notification_jobs(store: Store, adapters: dict[str, Any]) -> None:
    notifier = adapters.get("notifier")
    if notifier is None:
        return
    for job in store.due_notification_jobs():
        try:
            # MVP: 본문은 스케줄 이벤트 제목으로 구성
            ev_rows = store.list_scheduled_events(limit=200)
            title = "일정 알림"
            source_url = ""
            for ev in ev_rows:
                if f"{ev['source_provider']}:{ev['source_id']}" == job["scheduled_event_id"]:
                    title = f"곧 시작: {ev['title']}"
                    # gmail 유래 이벤트는 원본 메일 링크를 푸시에 포함
                    if ev["source_provider"] == "gmail" and ev["source_id"]:
                        try:
                            url = build_gmail_url(ev["source_id"])
                            source_url = url if is_safe_source_url(url) else ""
                        except ValueError:
                            pass
                    break
            notifier.send(title=title, body="", source_url=source_url)
            store.mark_job(job["id"], "delivered")
        except Exception as e:
            store.mark_job(job["id"], "failed", str(e)[:200])


def _maybe_poll_gmail(store: Store, settings: Settings, adapters: dict[str, Any]) -> None:
    auto = store.get_automation()
    if not auto.get("enabled"):
        return
    last = auto.get("last_gmail_poll_at")
    prefs = store.get_state("prefs", {})
    minutes = int(prefs.get("gmail_check_minutes") or 0)
    interval_secs = minutes * 60 if minutes in (30, 60) else settings.gmail_poll_interval_seconds
    if last:
        try:
            prev = datetime.fromisoformat(last)
            if datetime.now(UTC) - prev < timedelta(seconds=interval_secs):
                return
        except ValueError:
            pass
    gmail = adapters.get("gmail")
    if gmail is None:
        return
    try:
        ingested = _poll_gmail(store, settings, gmail)
        auto["last_gmail_poll_at"] = datetime.now(UTC).isoformat()
        auto["last_poll_result"] = f"ingested:{ingested}"
        auto["last_gmail_error"] = ""
    except Exception as e:
        auto["last_gmail_error"] = type(e).__name__
    store.set_automation(auto)


def _poll_gmail(store: Store, settings: Settings, gmail: Any) -> int:
    """Gmail adapter로 새 메시지를 수신함에 적재. 반환: 신규 건수."""
    ingested = 0
    for ref in gmail.list_messages(settings.gmail_query, 50):
        msg_id = ref.get("id", "")
        if not msg_id or store.has_message("gmail", msg_id):
            continue
        msg = gmail.get_message(msg_id)
        text = f"{msg.get('subject', '')}\n{msg.get('body_text', '')}"
        if store.save_message_extraction(
            "gmail", msg_id, msg.get("thread_id", ""), {}, "pending", source_text=text[:8000]
        ):
            ingested += 1
    return ingested


def _maybe_daily_radar(
    store: Store, settings: Settings, llm: Any, adapters: dict[str, Any]
) -> None:
    """KST 06:00 이후 최초 1회 radar 자동 수집 (자동화 플래그)."""
    auto = store.get_automation()
    if not auto.get("radar_enabled"):
        return
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    today = now_kst.date().isoformat()
    if now_kst.hour < 6:
        return
    prefs = store.get_state("prefs", {})
    last_radar = auto.get("last_radar_date")
    if last_radar == today:
        return
    if prefs.get("radar_interval") == "weekly" and last_radar:
        try:
            if (now_kst.date() - datetime.fromisoformat(last_radar).date()).days < 7:
                return
        except ValueError:
            pass
    if adapters.get("github") is None:
        return
    for p in settings.radar_platforms:
        if not store.radar_snapshot_exists(p, today):
            _create_and_run(
                store,
                settings,
                llm,
                adapters,
                "skill_radar",
                f"radar daily {p}",
                {"platform": p, "force": False},
            )
    auto["last_radar_date"] = today
    store.set_automation(auto)


app = create_app()
