"""Vercel serverless entry — create_app() 인스턴스를 ASGI app으로 노출."""

from __future__ import annotations

from .app import create_app

app = create_app()
