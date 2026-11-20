"""Vercel Python entrypoint — src 레이아웃이라 path를 추가한 뒤 app을 노출."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jobmate.api.serverless import app  # noqa: E402, F401
