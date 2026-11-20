"""코딩 작업 샌드박스 — seatbelt(macOS) / docker 실행 래퍼.

- seatbelt: `sandbox-exec` 프로필로 네트워크 차단 + workspace/임시 디렉터리
  외 파일 쓰기 차단 (macOS 전용, Linux에서는 무시됨)
- docker: `docker run --rm -v <cwd>:/work` 컨테이너 격리. API 키 계열
  환경변수만 `-e`로 전달한다.
- none: 래핑 없음.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable

logger = logging.getLogger("jobmate.sandbox")

MODES = ("none", "seatbelt", "docker")
_ENV_PASSTHROUGH = ("_API_KEY", "_TOKEN", "_SECRET")


def seatbelt_profile(workspace: str) -> str:
    ws = os.path.abspath(workspace)
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write*
  (subpath "{ws}")
  (subpath "/private/tmp")
  (subpath "/tmp")
  (subpath "/private/var/folders")
  (literal "/dev/null"))
"""


def make_wrapper(
    workspace: str,
    mode: str,
    image: str = "python:3.12-slim",
) -> Callable[[list[str]], list[str]] | None:
    """argv 리스트를 샌드박스 실행 형태로 변환하는 래퍼를 반환한다.

    mode가 none/미지원 플랫폼이면 None (호출자는 원래 argv 사용).
    """
    mode = (mode or "none").strip().lower()
    if mode == "none":
        return None
    ws = os.path.abspath(workspace)

    if mode == "seatbelt":
        if shutil.which("sandbox-exec") is None:
            logger.warning(
                "coding sandbox=seatbelt requested but sandbox-exec missing — unsandboxed"
            )
            return None
        profile = seatbelt_profile(ws)

        def wrap_seatbelt(argv: list[str]) -> list[str]:
            return ["sandbox-exec", "-p", profile, *argv]

        return wrap_seatbelt

    if mode == "docker":
        if shutil.which("docker") is None:
            logger.warning("coding sandbox=docker requested but docker missing — unsandboxed")
            return None

        def wrap_docker(argv: list[str]) -> list[str]:
            cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{ws}:/work:rw",
                "-w",
                "/work",
            ]
            for k, v in os.environ.items():
                if any(s in k for s in _ENV_PASSTHROUGH) and v:
                    cmd += ["-e", k]
            return [*cmd, image, *argv]

        return wrap_docker

    logger.warning("unknown coding sandbox mode=%s — unsandboxed", mode)
    return None
