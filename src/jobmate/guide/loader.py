"""가이드 계층 — 버전 관리된 프롬프트 로더."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache
def load_prompt(name: str) -> tuple[str, str]:
    """(내용, 버전). 파일 첫 줄의 `# vX.Y` 주석을 버전으로 사용."""
    path = PROMPTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    version = "0.0"
    for line in text.splitlines()[:5]:
        if line.startswith("# v"):
            version = line.split()[1].lstrip("v")
            break
    return text, version


def list_prompts() -> list[str]:
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))
