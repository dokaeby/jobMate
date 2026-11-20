"""function 단계 구현 — 모델 없이 결정적 검사/외부 부수효과."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..models import LinkStatus, NotificationStatus
from ..sensors import (
    build_gmail_url,
    git_apply_check,
    idempotency_key,
    is_safe_source_url,
    looks_like_diff,
    scan_secrets,
    validate_event,
)


@dataclass
class FunctionContext:
    state: Any
    store: Any
    settings: Any
    adapters: dict[str, Any]
    run_id: str
    llm: Any = None


def _prev_output(ctx: FunctionContext, stage_key: str) -> dict[str, Any]:
    return ctx.state.get("stage_outputs", {}).get(stage_key, {})


def auto_checks(ctx: FunctionContext) -> dict[str, Any]:
    """구현 산출물의 결정적 검사. diff 형식/시크릿/apply check/설정 명령."""
    impl = _prev_output(ctx, "implement")
    diff = impl.get("diff", "")
    checks: list[dict[str, Any]] = []
    passed = True

    if not diff.strip():
        checks.append({"name": "diff_present", "passed": False, "detail": "empty diff"})
        passed = False
    else:
        checks.append({"name": "diff_present", "passed": True})
        if not looks_like_diff(diff):
            checks.append(
                {"name": "diff_format", "passed": False, "detail": "no 'diff --git' header"}
            )
            passed = False
        else:
            checks.append({"name": "diff_format", "passed": True})
        ok, detail = git_apply_check(diff)
        checks.append({"name": "git_apply_check", "passed": ok, "detail": detail})
        passed = passed and ok

    hits = scan_secrets(diff)
    checks.append(
        {"name": "secret_scan", "passed": not hits, "detail": ",".join(hits) if hits else "clean"}
    )
    passed = passed and not hits

    for cmd in ctx.settings.check_commands:
        try:
            proc = subprocess.run(  # noqa: S603
                cmd.split(),
                capture_output=True,
                text=True,
                timeout=120,
                cwd=Path.cwd(),
            )
            ok = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as e:
            ok, proc = False, None
            detail = str(e)
        else:
            detail = (proc.stdout + proc.stderr)[-500:] if proc else ""
        checks.append({"name": f"cmd:{cmd}", "passed": ok, "detail": detail})
        passed = passed and ok

    return {"passed": passed, "checks": checks}


def _src(ctx: FunctionContext) -> tuple[str, str, str, str, str]:
    """(provider, source_id, connection_id, thread_id, source_url)."""
    provider = ctx.state.get("source_provider") or "gmail"
    source_id = ctx.state.get("source_id") or ctx.state.get("gmail_message_id") or ""
    conn = ctx.state.get("connection_id") or "default"
    thread = ctx.state.get("thread_id") or ""
    url = ctx.state.get("source_url") or ""
    if provider == "gmail" and not url:
        url = build_gmail_url(source_id, conn)
    return provider, source_id, conn, thread, url


def _event_key(title: str, start_at: str, end_at: str, timezone: str = "Asia/Seoul") -> str:
    """calendar-agent event_key 포팅 — 동일 일정 판별용 정규화 해시."""
    import hashlib
    import json
    from datetime import UTC
    from zoneinfo import ZoneInfo

    def aware(v: str) -> str:
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(timezone))
        return dt.astimezone(UTC).isoformat()

    data = [" ".join(title.casefold().split()), aware(start_at), aware(end_at)]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


def calendar_conflict_check(ctx: FunctionContext) -> dict[str, Any]:
    """calendar-agent RegistrationService의 중복/충돌 검사 (결정적)."""
    validated = _prev_output(ctx, "validate")
    ev = validated.get("event", {})
    if not ev:
        return {"status": "invalid"}
    provider, source_id, conn, thread, _ = _src(ctx)
    cal = ctx.adapters.get("calendar")

    # 동일 소스가 이미 등록/확인 대기면 중복
    existing = ctx.store.get_scheduled_by_source(provider, source_id)
    if existing and existing.get("status") in ("registered", "needs_confirmation"):
        return {"status": "duplicate", "calendar_event_id": existing.get("calendar_event_id", "")}

    sched_id = f"{provider}:{source_id}"
    start_at, end_at = ev.get("start_at", ""), ev.get("end_at", "")
    tz = ev.get("timezone", "Asia/Seoul")

    matches: list[dict[str, Any]] = []
    if cal is not None and hasattr(cal, "search_events"):
        try:
            matches = cal.search_events(start_at, end_at)
        except Exception as e:
            ctx.store.audit(
                ctx.run_id, "tool", "calendar_search_error", "", {"error": str(e)[:200]}
            )
            matches = []

    key = _event_key(ev.get("title", ""), start_at, end_at, tz)
    for m in matches:
        if m.get("status") == "cancelled":
            continue
        # 같은 마커(우리 멱등 키)거나 동일 title+시간 → 자기 자신 (복구 경로)
        marker = m.get("marker", "")
        if marker == idempotency_key(f"{provider}:{conn}", source_id):
            return {"status": "self", "calendar_event_id": m.get("id", "")}
        try:
            ms = m.get("start", {}).get("dateTime", "")
            me = m.get("end", {}).get("dateTime", "")
            mkey = _event_key(m.get("summary", ""), ms, me, tz)
        except Exception:
            mkey = ""
        if mkey and mkey == key:
            return {"status": "self", "calendar_event_id": m.get("id", "")}

    conflict = any(
        m.get("status") != "cancelled" and m.get("transparency") != "transparent" for m in matches
    )
    status = "conflict" if conflict else "clear"
    # 후보 레지스트리 기록 — 사람 확인 큐의 원천
    ctx.store.save_scheduled_event(
        event_id=sched_id,
        source_provider=provider,
        source_id=source_id,
        thread_id=thread,
        title=ev.get("title", ""),
        start_at=start_at,
        end_at=end_at,
        timezone=tz,
        location=ev.get("location", ""),
        summary=ev.get("summary", ""),
        status="needs_confirmation" if conflict else "candidate",
        confidence=float(ev.get("confidence", 0.0)),
        reason="calendar_conflict" if conflict else "",
        source_refs=source_id,
    )
    return {"status": status, "match_count": len(matches)}


def gmail_validate(ctx: FunctionContext) -> dict[str, Any]:
    """추출 후보를 결정적 검증한다."""
    ext = _prev_output(ctx, "extract")
    from ..models import ExtractedEvent

    try:
        ev = ExtractedEvent.model_validate(ext.get("event", ext))
    except Exception as e:
        return {"status": "invalid", "reason": str(e)[:200]}
    ok, reason = validate_event(ev)
    if not ok:
        # 검토 큐(scheduled_events)에 needs_review로 남겨 사람이 승인/수정할 수 있게 한다
        provider, source_id, _, thread, _ = _src(ctx)
        ctx.store.save_scheduled_event(
            event_id=f"{provider}:{source_id}",
            source_provider=provider,
            source_id=source_id,
            thread_id=thread,
            title=ev.title,
            start_at=ev.start_at,
            end_at=ev.end_at,
            timezone=ev.timezone,
            location=ev.location,
            summary=ev.summary,
            status="needs_review",
            confidence=ev.confidence,
            reason=reason,
            source_refs=source_id,
        )
    return {
        "status": "validated" if ok else "needs_review",
        "reason": reason,
        "event": ev.model_dump(),
    }


def gmail_register(ctx: FunctionContext) -> dict[str, Any]:
    """멱등 Calendar 등록 — pending 예약→marker 조회→생성→ID 저장.
    멀티채널: state의 source_provider/source_id 사용 (기본 gmail)."""
    calendar = ctx.adapters.get("calendar")
    if calendar is None:
        return {"status": "no_adapter"}
    validated = _prev_output(ctx, "validate")
    ev = validated.get("event", {})
    provider, source_id, conn, thread, _ = _src(ctx)
    key = idempotency_key(f"{provider}:{conn}", source_id)

    _, _, conn, _, source_url = _src(ctx)
    link = ctx.store.reserve_link(key, source_id, conn, source_url)
    if link is None:
        # 기존 링크 — 완료됐으면 duplicate, pending이면 marker 복구 시도
        existing = ctx.store.get_link_by_key(key)
        if existing is None:
            return {"status": "duplicate_pending", "idempotency_key": key}
        if existing.calendar_event_id:
            return {
                "status": "duplicate",
                "calendar_event_id": existing.calendar_event_id,
                "idempotency_key": key,
            }
        found = calendar.find_by_marker(key)
        if found:
            ctx.store.update_link(
                existing.id, status=LinkStatus.CALENDAR_CREATED, calendar_event_id=found
            )
            ctx.store.audit(ctx.run_id, "tool", "calendar_recovered", key, {"event_id": found})
            return {"status": "recovered", "calendar_event_id": found, "idempotency_key": key}
        return {"status": "duplicate_pending", "idempotency_key": key}

    # pending 링크 확보 후 외부 호출 — 타임아웃 시 marker 조회로 복구
    try:
        existing = calendar.find_by_marker(key)
        if existing:
            ctx.store.update_link(
                link.id, status=LinkStatus.CALENDAR_CREATED, calendar_event_id=existing
            )
            return {"status": "recovered", "calendar_event_id": existing, "idempotency_key": key}
        event_id = calendar.create_event(
            title=ev.get("title", ""),
            start_at=ev.get("start_at", ""),
            end_at=ev.get("end_at", ""),
            timezone=ev.get("timezone", "Asia/Seoul"),
            location=ev.get("location", ""),
            marker=key,
        )
    except Exception as e:
        ctx.store.update_link(
            link.id, status=LinkStatus.RETRY_WAIT, last_error=str(e)[:300], attempts_inc=True
        )
        ctx.store.audit(ctx.run_id, "tool", "calendar_error", key, {"error": str(e)[:200]})
        return {"status": "retry_wait", "error": str(e)[:300]}

    ctx.store.update_link(link.id, status=LinkStatus.CALENDAR_CREATED, calendar_event_id=event_id)
    ctx.store.audit(ctx.run_id, "tool", "calendar_created", key, {"event_id": event_id})
    # scheduled_events 레지스트리 갱신 (candidate → registered)
    provider, source_id, _, thread, _ = _src(ctx)
    ctx.store.save_scheduled_event(
        event_id=f"{provider}:{source_id}",
        source_provider=provider,
        source_id=source_id,
        thread_id=thread,
        calendar_event_id=event_id,
        title=ev.get("title", ""),
        start_at=ev.get("start_at", ""),
        end_at=ev.get("end_at", ""),
        timezone=ev.get("timezone", "Asia/Seoul"),
        location=ev.get("location", ""),
        summary=ev.get("summary", ""),
        status="registered",
        confidence=float(ev.get("confidence", 0.0)),
        source_refs=source_id,
    )
    return {
        "status": "created",
        "calendar_event_id": event_id,
        "idempotency_key": key,
        "scheduled_event_id": f"{provider}:{source_id}",
    }


def gmail_notify(ctx: FunctionContext) -> dict[str, Any]:
    """알림 큐잉 + 발송. 실패해도 Calendar 이벤트는 유지된다."""
    reg = _prev_output(ctx, "register")
    link_key = reg.get("idempotency_key", "")
    link = ctx.store.get_link_by_key(link_key) if link_key else None
    if link is None:
        return {"status": "no_link"}
    if reg.get("status") == "duplicate" and link.status == LinkStatus.NOTIFIED:
        return {"status": "already_notified"}

    ev = _prev_output(ctx, "validate").get("event", {})
    _, _, _, _, source_url = _src(ctx)
    if not source_url or not is_safe_source_url(source_url):
        source_url = ""
    notifier = ctx.adapters.get("notifier")
    if notifier is None:
        return {"status": "no_adapter"}
    n = ctx.store.add_notification(
        title=f"일정 등록: {ev.get('title', '')}",
        body=ev.get("start_at", ""),
        source_url=source_url,
        run_id=ctx.run_id,
        link_id=link.id,
    )
    try:
        notifier.send(title=n.title, body=n.body, source_url=n.source_url)
        ctx.store.set_notification_status(n.id, NotificationStatus.SENT)
        ctx.store.update_link(link.id, status=LinkStatus.NOTIFIED, notification_id=n.id)
        jobs = _schedule_event_notifications(ctx, ev, reg)
        _send_web_push(ctx, n)
        return {"status": "notified", "notification_id": n.id, "notification_jobs": jobs}
    except Exception as e:
        ctx.store.set_notification_status(n.id, NotificationStatus.FAILED)
        ctx.store.update_link(
            link.id,
            status=LinkStatus.NOTIFICATION_QUEUED,
            notification_id=n.id,
            last_error=str(e)[:300],
        )
        ctx.store.audit(ctx.run_id, "tool", "notify_failed", link.id, {"error": str(e)[:200]})
        return {"status": "notify_failed", "notification_id": n.id, "error": str(e)[:300]}


def _send_web_push(ctx: FunctionContext, n: Any) -> None:
    """Web Push 채널 — best-effort. 실패해도 알림 상태는 SENT 유지."""
    push = ctx.adapters.get("webpush")
    if push is None:
        return
    try:
        push.send(title=n.title, body=n.body, source_url=n.source_url)
    except Exception as e:
        ctx.store.audit(ctx.run_id, "tool", "webpush_failed", str(n.id), {"error": str(e)[:200]})


def _schedule_event_notifications(
    ctx: FunctionContext, ev: dict[str, Any], reg: dict[str, Any]
) -> list[str]:
    """calendar-agent NotificationScheduler 포팅 — 시작 2h/1h 전 예약."""
    sched_id = reg.get("scheduled_event_id") or ""
    if not sched_id:
        provider, source_id, _, _, _ = _src(ctx)
        sched_id = f"{provider}:{source_id}"
    try:
        start = datetime.fromisoformat(ev.get("start_at", ""))
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
    except ValueError:
        return []
    now = datetime.now(UTC)
    jobs: list[str] = []
    # 90분 이내 등록이면 2h 알림 생략, 30분 이내면 1h 알림 생략 (원본 규칙)
    if (start - now).total_seconds() > 90 * 60:
        jobs.append(
            ctx.store.save_notification_job(
                sched_id, "push", (start - timedelta(hours=2)).isoformat()
            )
        )
    if (start - now).total_seconds() > 30 * 60:
        jobs.append(
            ctx.store.save_notification_job(
                sched_id, "email", (start - timedelta(hours=1)).isoformat()
            )
        )
    return jobs


# ---------- skillRadar (radar_*) ----------


def _radar_platforms(ctx: FunctionContext) -> list[str]:
    p = ctx.state.get("platform") or "all"
    if p and p != "all":
        return [p]
    return list(ctx.settings.radar_platforms)


def radar_collect(ctx: FunctionContext) -> dict[str, Any]:
    """GitHub search + README 수집. adapter 'github' 필요."""
    from ..radar import load_queries

    gh = ctx.adapters.get("github")
    if gh is None:
        return {"status": "no_adapter", "repos": []}
    platforms = _radar_platforms(ctx)
    queries = load_queries(ctx.settings.radar_queries_path)
    repos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for platform in platforms:
        for q in queries.get(platform, []):
            for hit in gh.search_repositories(q, ctx.settings.radar_per_query):
                name = hit.get("full_name", "")
                if not name or name in seen:
                    continue
                seen.add(name)
                hit["platform"] = platform
                hit["readme"] = gh.fetch_readme(name)
                repos.append(hit)
    return {"status": "ok", "repos": repos, "platforms": platforms}


def radar_dedupe(ctx: FunctionContext) -> dict[str, Any]:
    """archived/fork/중복 제거 + 상한."""
    repos = (_prev_output(ctx, "collect") or {}).get("repos", [])
    seen: set[str] = set()
    out = []
    for r in repos:
        name = r.get("full_name", "")
        if not name or name in seen:
            continue
        if r.get("archived") or r.get("fork"):
            continue
        seen.add(name)
        out.append(r)
        if len(out) >= ctx.settings.radar_max_repos:
            break
    return {"status": "ok", "repos": out, "dropped": len(repos) - len(out)}


def radar_summarize(ctx: FunctionContext) -> dict[str, Any]:
    """repo별 3개 언어 요약 — content_hash 캐시 우선, LLM은 default 티어."""
    from ..guide.loader import load_prompt
    from ..models import RepoSummary
    from ..radar import content_hash

    repos = (_prev_output(ctx, "dedupe") or {}).get("repos", [])
    if not repos:
        return {"status": "ok", "repos": []}
    if ctx.llm is None:
        return {"status": "no_llm", "repos": repos}

    model = ctx.settings.model_for("_radar", "default")
    system, _pv = load_prompt("radar_summarize")
    out_repos: list[dict[str, Any]] = []
    tokens = {"tokens_in": 0, "tokens_out": 0}
    for r in repos:
        item = dict(r)
        chash = content_hash(r.get("description"), r.get("readme", ""))
        item["content_hash"] = chash
        cached = ctx.store.radar_cached_repo(r.get("full_name", ""), chash)
        if cached:
            item.update(
                {
                    k: cached[k]
                    for k in (
                        "summary_en",
                        "summary_zh",
                        "summary_ko",
                        "taxonomy",
                        "taxonomy_confidence",
                        "summary_status",
                    )
                }
            )
            item["summary_source"] = "cache"
            out_repos.append(item)
            continue
        user = (
            f"repo: {r.get('full_name')}\n"
            f"url: {r.get('html_url')}\n"
            f"description: {r.get('description') or ''}\n"
            f"language: {r.get('language') or ''}\n"
            f"topics: {', '.join(r.get('topics') or [])}\n"
            f"license: {r.get('license') or ''}\n"
            f"stars: {r.get('stars', 0)}\n\n"
            f"README:\n{(r.get('readme') or '')[:3000]}"
        )
        try:
            parsed, usage = ctx.llm.complete(
                model=model, system=system, user=user, schema=RepoSummary
            )
            summ = parsed if isinstance(parsed, RepoSummary) else RepoSummary.model_validate(parsed)
            item.update(summ.model_dump())
            item["summary_source"] = "llm"
            tokens["tokens_in"] += usage.get("tokens_in", 0)
            tokens["tokens_out"] += usage.get("tokens_out", 0)
        except Exception as e:
            item.update(
                {
                    "summary_en": r.get("description") or "",
                    "summary_zh": "",
                    "summary_ko": r.get("description") or "",
                    "taxonomy": "other",
                    "taxonomy_confidence": 0.0,
                    "summary_status": "fallback",
                }
            )
            item["summary_source"] = "fallback"
            ctx.store.audit(
                ctx.run_id,
                "tool",
                "radar_summarize_error",
                r.get("full_name", ""),
                {"error": str(e)[:200]},
            )
        item["readme_excerpt"] = (r.get("readme") or "")[:3000]
        out_repos.append(item)
    return {"status": "ok", "repos": out_repos, **tokens}


def radar_rank(ctx: FunctionContext) -> dict[str, Any]:
    """stars + 증가량 + 라이선스/분류 신뢰도 점수로 정렬."""
    from zoneinfo import ZoneInfo

    from ..radar import stars_delta

    repos = (_prev_output(ctx, "summarize") or {}).get("repos", [])
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    # 플랫폼별 prev stars — publish가 플랫폼 단위로 snapshot을 쓰므로
    # 여기서는 repo의 platform 필드 기준으로 조회
    prev_cache: dict[str, dict[str, int]] = {}
    for r in repos:
        p = r.get("platform", "")
        if p not in prev_cache:
            prev_cache[p] = ctx.store.radar_prev_stars(p, today)
        prev = prev_cache[p].get(r.get("full_name", ""))
        r["stars_today"] = stars_delta(int(r.get("stars", 0)), prev)
        score = (
            int(r.get("stars", 0))
            + (r["stars_today"] or 0) * 2
            + (50 if r.get("license") else 0)
            + float(r.get("taxonomy_confidence", 0.0)) * 10
        )
        r["rank_score"] = score
    repos.sort(key=lambda x: x["rank_score"], reverse=True)
    for i, r in enumerate(repos, 1):
        r["rank"] = i
    platforms = sorted({r.get("platform", "") for r in repos})
    return {"status": "ok", "repos": repos, "date": today, "platforms": platforms}


def radar_publish(ctx: FunctionContext) -> dict[str, Any]:
    """승인된 결과를 플랫폼별 snapshot/item으로 저장 + 알림."""
    approval = ctx.state.get("approval") or {}
    if approval.get("decision") != "approve":
        return {"status": "rejected"}
    rank_out = _prev_output(ctx, "rank") or {}
    repos = rank_out.get("repos", [])
    date_str = rank_out.get("date") or ""
    force = bool(ctx.state.get("force"))
    notifier = ctx.adapters.get("notifier")

    by_platform: dict[str, list[dict[str, Any]]] = {}
    for r in repos:
        by_platform.setdefault(r.get("platform", "unknown"), []).append(r)

    written: dict[str, int] = {}
    for platform, items in by_platform.items():
        if ctx.store.radar_snapshot_exists(platform, date_str):
            if not force:
                continue
            snap = ctx.store.radar_snapshot(platform, date_str)
            if snap:
                ctx.store.delete_radar_snapshot(int(snap["id"]))
        snap_id = ctx.store.save_radar_snapshot(
            platform, date_str, {"count": len(items), "run_id": ctx.run_id}
        )
        for it in items:
            ctx.store.add_radar_item(
                snap_id,
                {
                    "rank": it.get("rank", 0),
                    "repo_full_name": it.get("full_name", ""),
                    "repo_url": it.get("html_url", ""),
                    "description": it.get("description") or "",
                    "stars": it.get("stars", 0),
                    "language": it.get("language") or "",
                    "topics": it.get("topics") or [],
                    "readme_excerpt": it.get("readme_excerpt", ""),
                    "summary_en": it.get("summary_en", ""),
                    "summary_zh": it.get("summary_zh", ""),
                    "summary_ko": it.get("summary_ko", ""),
                    "content_hash": it.get("content_hash", ""),
                    "stars_today": it.get("stars_today"),
                    "license": it.get("license") or "",
                    "taxonomy": it.get("taxonomy", ""),
                    "taxonomy_confidence": it.get("taxonomy_confidence", 0.0),
                    "rank_score": it.get("rank_score", 0.0),
                    "summary_status": it.get("summary_status", ""),
                },
            )
        written[platform] = len(items)

    if notifier is not None and written:
        total = sum(written.values())
        try:
            notifier.send(
                title=f"skillRadar 수집 완료 ({date_str})",
                body=f"{total}개 저장소 스냅샷 저장: "
                + ", ".join(f"{k} {v}" for k, v in written.items()),
                source_url="",
            )
        except Exception as e:
            ctx.store.audit(ctx.run_id, "tool", "radar_notify_error", "", {"error": str(e)[:200]})
    return {"status": "published", "written": written, "date": date_str}


FUNCTIONS: dict[str, Callable[[FunctionContext], dict[str, Any]]] = {
    "auto_checks": auto_checks,
    "gmail_validate": gmail_validate,
    "gmail_register": gmail_register,
    "gmail_notify": gmail_notify,
    "calendar_conflict_check": calendar_conflict_check,
    "radar_collect": radar_collect,
    "radar_dedupe": radar_dedupe,
    "radar_summarize": radar_summarize,
    "radar_rank": radar_rank,
    "radar_publish": radar_publish,
}
