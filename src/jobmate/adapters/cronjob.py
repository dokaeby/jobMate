"""cron-job.org REST API 클라이언트 — 대시보드 설정을 실제 cron 스케줄에 반영.

PATCH /jobs/{jobId} {"job": {"schedule": {...}}} 로 부분 갱신.
인증: Authorization: Bearer <api_key> (콘솔 Settings에서 발급).
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.cron-job.org"
_WILDCARD = [-1]


class CronJobOrgError(Exception):
    pass


def make_schedule(
    *,
    minutes: list[int],
    hours: list[int] | None = None,
    wdays: list[int] | None = None,
    timezone: str = "Asia/Seoul",
) -> dict[str, Any]:
    """cron-job.org JobSchedule 객체 생성. -1은 wildcard(매)."""
    return {
        "timezone": timezone,
        "expiresAt": 0,
        "hours": hours if hours is not None else list(_WILDCARD),
        "mdays": list(_WILDCARD),
        "minutes": minutes,
        "months": list(_WILDCARD),
        "wdays": wdays if wdays is not None else list(_WILDCARD),
    }


def interval_schedule(minutes: int, timezone: str = "Asia/Seoul") -> dict[str, Any]:
    """N분 간격 — 30→[0,30], 60→[0]."""
    if minutes == 30:
        return make_schedule(minutes=[0, 30], timezone=timezone)
    if minutes == 60:
        return make_schedule(minutes=[0], timezone=timezone)
    raise CronJobOrgError(f"unsupported interval: {minutes}m")


def daily_at(hour: int, minute: int = 0, timezone: str = "Asia/Seoul") -> dict[str, Any]:
    return make_schedule(minutes=[minute], hours=[hour], timezone=timezone)


def weekly_at(
    hour: int, minute: int = 0, wday: int = 1, timezone: str = "Asia/Seoul"
) -> dict[str, Any]:
    """매주 특정 요일 — wday: 0=일 ~ 6=토."""
    return make_schedule(minutes=[minute], hours=[hour], wdays=[wday], timezone=timezone)


class CronJobOrgClient:
    """cron-job.org API — jobId 단위 PATCH."""

    def __init__(
        self,
        api_key: str,
        job_id: str,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise CronJobOrgError("api_key required")
        if not job_id:
            raise CronJobOrgError("job_id required")
        self._job_id = job_id
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=15)

    @classmethod
    def for_settings(cls, settings: Any) -> CronJobOrgClient | None:
        """설정에서 클라이언트 생성. job_id 없으면 None."""
        import os

        api_key = os.environ.get(settings.cronjob_api_key_env, "")
        job_id = getattr(settings, "cronjob_job_id", "") or ""
        if not api_key or not job_id:
            return None
        return cls(api_key, job_id, settings.cronjob_base_url)

    def update_schedule(self, schedule: dict[str, Any]) -> dict[str, Any]:
        r = self._client.patch(
            f"/jobs/{self._job_id}", headers=self._headers, json={"job": {"schedule": schedule}}
        )
        if r.status_code != 200:
            raise CronJobOrgError(f"cron-job.org PATCH failed: {r.status_code} {r.text[:200]}")
        return r.json()

    def get_job(self) -> dict[str, Any]:
        r = self._client.get(f"/jobs/{self._job_id}", headers=self._headers)
        if r.status_code != 200:
            raise CronJobOrgError(f"cron-job.org GET failed: {r.status_code} {r.text[:200]}")
        return r.json()
