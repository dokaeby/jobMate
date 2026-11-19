"""diff 형식·적용 가능성 검사."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_DIFF_HEADER = re.compile(r"^diff --git ", re.M)


def looks_like_diff(text: str) -> bool:
    return bool(_DIFF_HEADER.search(text))


def git_apply_check(diff_text: str, repo: Path | None = None) -> tuple[bool, str]:
    """git apply --check 로 적용 가능성만 검사한다(실제 적용 안 함)."""
    if repo is None:
        repo = Path.cwd()
    if not repo.joinpath(".git").exists():
        return True, "not_a_git_repo: skip apply check"
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as f:
        f.write(diff_text)
        tmp = f.name
    try:
        proc = subprocess.run(  # noqa: S603
            [shutil.which("git") or "git", "apply", "--check", tmp],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0, proc.stderr.strip()[:500]
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"check_error: {e}"
    finally:
        Path(tmp).unlink(missing_ok=True)
