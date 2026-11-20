
from jobmate.adapters.fakes import FakeCalendar, FakeGmail, FakeNotifier
from jobmate.models import ExtractedEvent, LinkStatus
from jobmate.sensors import idempotency_key
from jobmate.workflows.registry_builder import build_runner

from .helpers import make_env, scripted_llm


def _env_with_adapters(**kw):
    settings, store = make_env(**kw)
    gmail = FakeGmail([{"id": "msg1", "subject": "회의 안내",
                        "body_text": "내일 오후 3시 회의", "from_addr": "a@b.c"}])
    cal = FakeCalendar()
    notifier = FakeNotifier()
    adapters = {"gmail": gmail, "calendar": cal, "notifier": notifier}
    return settings, store, gmail, cal, notifier, adapters


def _extract_output(**kw):
    ev = dict(title="팀 회의", start_at="2026-01-05T15:00:00+09:00",
              end_at="2026-01-05T16:00:00+09:00", timezone="Asia/Seoul",
              confidence=0.9)
    ev.update(kw)
    return {"ExtractedEvent": [ExtractedEvent(**ev)]}


def _run(store, settings, llm, adapters, msg_id="msg1"):
    run = store.create_run("gmail_calendar", "0.1.0",
                           {"gmail_message_id": msg_id}, trace_id="t")
    runner = build_runner("gmail_calendar", store=store, llm=llm,
                          settings=settings, adapters=adapters)
    state = runner.invoke(run.id, {"task": "poll"},
                          gmail_ctx={"gmail_message_id": msg_id})
    return run.id, runner, state


def test_full_flow_creates_event_and_notifies():
    settings, store, gmail, cal, notifier, adapters = _env_with_adapters()
    llm = scripted_llm(_extract_output())
    run_id, _, _ = _run(store, settings, llm, adapters)
    links = store.list_links()
    assert len(links) == 1
    link = links[0]
    assert link.status == LinkStatus.NOTIFIED
    assert link.calendar_event_id == "evt_1"
    assert len(notifier.sent) == 1
    assert cal.create_calls == 1


def test_duplicate_message_no_second_event():
    settings, store, gmail, cal, notifier, adapters = _env_with_adapters()
    llm = scripted_llm(_extract_output())
    _run(store, settings, llm, adapters, "msg1")
    _run(store, settings, llm, adapters, "msg1")
    assert cal.create_calls == 1
    statuses = [x.status for x in store.list_links()]
    assert LinkStatus.DUPLICATE in statuses or len(store.list_links()) == 1


def test_ambiguous_event_needs_review_no_calendar():
    settings, store, gmail, cal, notifier, adapters = _env_with_adapters()
    llm = scripted_llm(_extract_output(is_ambiguous=True))
    run_id, _, _ = _run(store, settings, llm, adapters)
    assert cal.create_calls == 0
    assert store.list_links() == []


def test_recurring_event_rejected():
    settings, store, gmail, cal, notifier, adapters = _env_with_adapters()
    llm = scripted_llm(_extract_output(is_recurring=True))
    _run(store, settings, llm, adapters)
    assert cal.create_calls == 0


def test_calendar_timeout_recovers_via_marker():
    settings, store, gmail, cal, notifier, adapters = _env_with_adapters()
    cal.timeout_next = TimeoutError("api timeout")
    llm = scripted_llm(_extract_output())
    run_id, _, _ = _run(store, settings, llm, adapters)
    # 첫 시도: 타임아웃 → retry_wait, 이벤트는 marker로 생성됨
    links = store.list_links()
    assert links[0].status == LinkStatus.RETRY_WAIT
    # 재시도: marker 조회로 복구 → notify까지
    llm2 = scripted_llm(_extract_output())
    run2 = store.create_run("gmail_calendar", "0.1.0",
                            {"gmail_message_id": "msg1"}, trace_id="t2")
    runner = build_runner("gmail_calendar", store=store, llm=llm2,
                          settings=settings, adapters=adapters)
    runner.invoke(run2.id, {"task": "poll"},
                  gmail_ctx={"gmail_message_id": "msg1"})
    link = store.get_link_by_key(idempotency_key("gmail:default", "msg1"))
    assert link.status in (LinkStatus.NOTIFIED, LinkStatus.CALENDAR_CREATED,
                           LinkStatus.NOTIFICATION_QUEUED)
    assert cal.create_calls == 1  # 재생성 없음


def test_webpush_sent_on_calendar_register():
    settings, store, gmail, cal, notifier, adapters = _env_with_adapters()
    sent = []

    class FakePush:
        def send(self, *, title, body, source_url):
            sent.append({"title": title, "body": body,
                         "source_url": source_url})

    adapters["webpush"] = FakePush()
    llm = scripted_llm(_extract_output())
    _run(store, settings, llm, adapters)
    assert len(sent) == 1
    assert sent[0]["title"].startswith("일정 등록")


def test_notify_failure_keeps_calendar_event():
    settings, store, gmail, cal, notifier, adapters = _env_with_adapters()
    notifier.fail = True
    llm = scripted_llm(_extract_output())
    _run(store, settings, llm, adapters)
    link = store.list_links()[0]
    assert link.status == LinkStatus.NOTIFICATION_QUEUED
    assert link.calendar_event_id == "evt_1"  # 이벤트 유지
    assert cal.create_calls == 1
