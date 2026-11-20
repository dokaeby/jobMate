"""UI 자동화 테스트 — 실제 uvicorn 서버 + headless Chromium (Playwright).

docs/jobMate_v1.0.0/13·14 의 시나리오 중 현재 구현된 화면 흐름을 커버한다.
LLM/외부 어댑터 없이 빈 DB + fake 어댑터로 결정적으로 동작한다.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, sync_playwright

from jobmate.api.app import create_app
from jobmate.config import Settings
from jobmate.store.db import Store


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """격리된 임시 DB로 실제 uvicorn 서버를 띄운다."""
    tmp = tmp_path_factory.mktemp("ui")
    settings = Settings(
        db_path=tmp / "jm.db",
        radar_platforms=[],
        worker_interval_seconds=60,
        gmail_poll_interval_seconds=3600,
        coding_tool="none",
    )
    store = Store(str(settings.db_path))
    app = create_app(settings=settings, store=store, llm=None, adapters={})

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    httpx.get(url + "/api/v1/health", timeout=2)
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def page(base_url: str) -> Iterator[Page]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(base_url)
        pg.wait_for_selector(".nav-item")
        yield pg
        browser.close()


def _open_view(page: Page, view: str, title: str) -> None:
    page.click(f'.nav-item[data-view="{view}"]')
    section = page.locator(f"#view-{view}")
    assert section.is_visible()
    assert page.locator(f"#view-{view} .page-title").inner_text() == title


def test_sidebar_navigation(page: Page) -> None:
    """사이드바 5개 메뉴가 각각 우측 화면을 전환한다."""
    for view, title in [
        ("gmail", "Gmail Checker"),
        ("coding", "코드 어댑터"),
        ("calendar", "Calendar"),
        ("radar", "Skill Radar"),
        ("runs", "실행"),
    ]:
        _open_view(page, view, title)


def _wait_text(page: Page, selector: str, text: str) -> None:
    page.wait_for_function(
        f"document.querySelector('{selector}')"
        f" && document.querySelector('{selector}').innerText.includes('{text}')",
        timeout=5000,
    )


def test_gmail_view_empty_state(page: Page) -> None:
    """메시지가 없으면 빈 상태 문구를 표시한다."""
    _wait_text(page, "#gmail-cards", "메시지 없음")


def test_calendar_view_empty_state(page: Page) -> None:
    page.click('.nav-item[data-view="calendar"]')
    _wait_text(page, "#calendar-cards", "등록된 일정 없음")


def test_radar_view_platforms(page: Page) -> None:
    page.click('.nav-item[data-view="radar"]')
    options = page.locator("#radar-platform option").all_inner_texts()
    assert options == ["claude", "chatgpt", "gemini"]


def test_runs_view_table(page: Page) -> None:
    page.click('.nav-item[data-view="runs"]')
    _wait_text(page, "#runs-body", "실행 없음")


def test_coding_view_tool_options(page: Page) -> None:
    """코드 어댑터 뷰에 CLI 목록이 로드된다."""
    page.click('.nav-item[data-view="coding"]')
    page.wait_for_function("document.querySelectorAll('#coding-tool option').length > 0")
    values = page.locator("#coding-tool option").evaluate_all("els => els.map(e => e.value)")
    assert set(values) == {"claude", "codex", "antigravity", "opencode"}


def test_settings_dialog_save(page: Page, base_url: str) -> None:
    """설정 다이얼로그에서 값을 바꿔 저장하면 API에 반영된다."""
    page.click("#settings-open")
    page.wait_for_function("document.querySelector('#settings-dialog').open")
    page.wait_for_function("document.querySelector('#set-gmail-minutes').value !== ''")
    page.select_option("#set-gmail-minutes", "60")
    page.select_option("#set-radar-interval", "weekly")
    page.select_option("#set-coding-tool", "codex")
    page.click("#settings-save")
    page.wait_for_function(
        "document.querySelector('#settings-status').textContent.includes('저장됨')",
        timeout=5000,
    )
    resp = page.request.get(base_url + "/api/v1/settings")
    prefs = resp.json()
    assert prefs["gmail_check_minutes"] == 60
    assert prefs["radar_interval"] == "weekly"
    assert prefs["coding_tool"] == "codex"


def test_push_toggle_without_vapid(page: Page) -> None:
    """VAPID 키 미설정 시 알림 켜기가 alert로 안내하고 화면이 유지된다."""
    messages: list[str] = []
    page.on("dialog", lambda d: (messages.append(d.message), d.dismiss()))
    page.click("#push-toggle")
    page.wait_for_timeout(1000)
    assert any("VAPID" in m or "지원" in m for m in messages)
    assert page.locator("#push-toggle").is_visible()
