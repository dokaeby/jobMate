"""테스트/로컬용 fake 어댑터 — 결정적."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class FakeGmail:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages = messages or []
        self.fetched: list[str] = []

    def list_messages(self, query: str = "", max_results: int = 10) -> list[dict[str, Any]]:
        return self._messages[:max_results]

    def get_message(self, message_id: str) -> dict[str, Any]:
        self.fetched.append(message_id)
        for m in self._messages:
            if m.get("id") == message_id:
                return m
        return {
            "id": message_id,
            "subject": "",
            "body_text": "",
            "from_addr": "",
            "internal_date": "",
        }


class FakeCalendar:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self._by_marker: dict[str, str] = {}
        self.create_calls = 0
        self.fail_next: Exception | None = None
        self.timeout_next: Exception | None = None
        self.search_calls = 0

    def create_event(
        self,
        *,
        title: str,
        start_at: str,
        end_at: str,
        timezone: str,
        location: str,
        marker: str,
        event_id: str | None = None,
    ) -> str:
        self.create_calls += 1
        if self.timeout_next is not None:
            # 결과 불명 타임아웃 — 이벤트는 생성됐을 수 있음
            eid = event_id or f"evt_{self.create_calls}"
            self.events[eid] = {"title": title, "marker": marker, "start": start_at, "end": end_at}
            self._by_marker[marker] = eid
            raise self.timeout_next
        if self.fail_next is not None:
            raise self.fail_next
        eid = event_id or f"evt_{self.create_calls}"
        self.events[eid] = {
            "title": title,
            "marker": marker,
            "start": start_at,
            "end": end_at,
            "timezone": timezone,
            "location": location,
        }
        self._by_marker[marker] = eid
        return eid

    def find_by_marker(self, marker: str) -> str | None:
        return self._by_marker.get(marker)

    def search_events(self, start_at: str, end_at: str) -> list[dict[str, Any]]:
        """시간대 겹치는 비취소 이벤트 반환 (calendar-agent search_events 대응)."""
        self.search_calls += 1
        try:
            s = datetime.fromisoformat(start_at)
            e = datetime.fromisoformat(end_at)
        except ValueError:
            return []
        out = []
        for eid, ev in self.events.items():
            if ev.get("status") == "cancelled":
                continue
            try:
                es = datetime.fromisoformat(str(ev["start"]))
                ee = datetime.fromisoformat(str(ev["end"]))
            except (KeyError, ValueError):
                continue
            if es < e and ee > s:
                out.append(
                    {
                        "id": eid,
                        "summary": ev.get("title", ""),
                        "status": "confirmed",
                        "transparency": "opaque",
                        "start": {"dateTime": str(ev["start"])},
                        "end": {"dateTime": str(ev["end"])},
                        "marker": ev.get("marker", ""),
                    }
                )
        return out

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        ev = self.events.get(event_id)
        if ev is None:
            return None
        return {
            "id": event_id,
            "summary": ev.get("title", ""),
            "status": ev.get("status", "confirmed"),
            "start": {"dateTime": str(ev.get("start", ""))},
            "end": {"dateTime": str(ev.get("end", ""))},
        }


class FakeNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict[str, str]] = []
        self.fail = fail

    def send(self, *, title: str, body: str, source_url: str) -> None:
        if self.fail:
            raise RuntimeError("push failed")
        self.sent.append({"title": title, "body": body, "source_url": source_url})


class FakeGitHub:
    """결정적 GitHub fake — search_repositories/fetch_readme."""

    def __init__(
        self,
        repos: dict[str, list[dict[str, Any]]] | None = None,
        readmes: dict[str, str] | None = None,
    ) -> None:
        self._repos = repos or {}
        self._readmes = readmes or {}
        self.queries: list[str] = []

    def search_repositories(self, query: str, per_page: int = 30) -> list[dict[str, Any]]:
        self.queries.append(query)
        return list(self._repos.get(query) or self._repos.get("*", []))[:per_page]

    def fetch_readme(self, full_name: str) -> str:
        return self._readmes.get(full_name, "")
