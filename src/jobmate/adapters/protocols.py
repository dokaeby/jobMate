"""외부 어댑터 Protocol — fake/real 모두 동일 계약."""
from __future__ import annotations

from typing import Any, Protocol


class GmailClient(Protocol):
    def list_messages(self, query: str = "", max_results: int = 10) -> list[dict[str, Any]]: ...
    def get_message(self, message_id: str) -> dict[str, Any]: ...


class CalendarClient(Protocol):
    def create_event(self, *, title: str, start_at: str, end_at: str,
                     timezone: str, location: str, marker: str,
                     event_id: str | None = None) -> str: ...
    def find_by_marker(self, marker: str) -> str | None: ...
    def search_events(self, start_at: str, end_at: str) -> list[dict[str, Any]]: ...
    def get_event(self, event_id: str) -> dict[str, Any] | None: ...


class Notifier(Protocol):
    def send(self, *, title: str, body: str, source_url: str) -> None: ...


class GitHubClient(Protocol):
    """skillRadar 수집용 — 동기 httpx 기반."""

    def search_repositories(self, query: str, per_page: int = 30) -> list[dict[str, Any]]: ...
    def fetch_readme(self, full_name: str) -> str: ...


class GmailMessage(Protocol):
    id: str
    subject: str
    body_text: str
    from_addr: str
