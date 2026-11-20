"""LLM 클라이언트 — OpenAI 호환 chat/completions + fake 주입."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..models import ModelConfig

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    """모든 LLM 호출의 경계. fake 구현이 동일 계약을 따른다."""

    def complete(
        self, *, model: ModelConfig, system: str, user: str, schema: type[T] | None = None
    ) -> tuple[T | dict[str, Any] | str, dict[str, int]]:
        """반환: (파싱된 출력, usage{tokens_in,tokens_out})."""
        ...


def extract_json(text: str) -> Any:
    """모델 출력에서 JSON 객체/배열을 추출한다. 실패 시 ValueError."""
    # 코드펜스 우선
    m = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.S)
    candidates = [m.group(1)] if m else []
    # 첫 { 또는 [ ~ 마지막 } 또는 ]
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start >= 0:
        end_candidates = [i for i in (text.rfind("}"), text.rfind("]")) if i >= 0]
        end = max(end_candidates) if end_candidates else -1
        if end > start:
            candidates.append(text[start : end + 1])
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON found in model output")


class OpenAICompatLLM:
    """OpenAI 호환 chat/completions 클라이언트. httpx + 재시도."""

    def __init__(self, max_retries: int = 2, backoff: float = 1.0) -> None:
        self._max_retries = max_retries
        self._backoff = backoff

    def complete(
        self, *, model: ModelConfig, system: str, user: str, schema: type[T] | None = None
    ) -> tuple[Any, dict[str, int]]:
        api_key = os.environ.get(model.api_key_env, "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload: dict[str, Any] = {
            "model": model.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": model.temperature,
            "max_tokens": model.max_tokens,
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}
        url = f"{model.base_url.rstrip('/')}/chat/completions"
        last_err: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                with httpx.Client(timeout=model.timeout_seconds) as client:
                    resp = client.post(url, headers=headers, json=payload)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                    time.sleep(self._backoff * (2**attempt))
                    continue
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                usage = {
                    "tokens_in": data.get("usage", {}).get("prompt_tokens", 0),
                    "tokens_out": data.get("usage", {}).get("completion_tokens", 0),
                }
                if schema is not None:
                    parsed = schema.model_validate(extract_json(text))
                    return parsed, usage
                try:
                    return extract_json(text), usage
                except ValueError:
                    return text, usage
            except (httpx.HTTPError, KeyError, IndexError, ValueError, ValidationError) as e:
                last_err = e
                if attempt < self._max_retries:
                    time.sleep(self._backoff * (2**attempt))
                    continue
                raise LLMError(f"llm call failed after {attempt + 1} attempts: {e}") from e
        raise LLMError(f"llm call failed: {last_err}")


class FakeLLM:
    """결정적 테스트용 fake. script: (system,user)->응답 callable 또는 큐."""

    def __init__(self, handler: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._handler = handler or (lambda **kw: {"result": "ok"})

    def complete(
        self, *, model: ModelConfig, system: str, user: str, schema: type[T] | None = None
    ) -> tuple[Any, dict[str, int]]:
        self.calls.append({"model": model.model, "system": system[:200], "user": user[:500]})
        out = self._handler(system=system, user=user, model=model, schema=schema)
        result: Any
        if isinstance(out, BaseModel):
            result = out
        elif schema is not None and isinstance(out, dict):
            result = schema.model_validate(out)
        elif schema is not None and isinstance(out, str):
            result = schema.model_validate(extract_json(out))
        else:
            result = out
        return result, {"tokens_in": 10, "tokens_out": 10}
