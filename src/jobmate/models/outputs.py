"""LLM 단계 출력 스키마 — 경계 검증용 Pydantic 모델."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChangeProposal(BaseModel):
    """구현 에이전트 산출물. diff 텍스트만 — 실제 적용은 하지 않는다."""

    summary: str
    diff: str = ""
    done_state: Literal["DONE", "DONE_WITH_CONCERNS", "BLOCKED", "NEEDS_CONTEXT"] = "DONE"


class AnalysisResult(BaseModel):
    requirements: str
    scope: str
    risks: list[str] = Field(default_factory=list)
    done_state: Literal["DONE", "DONE_WITH_CONCERNS", "BLOCKED", "NEEDS_CONTEXT"] = "DONE"


class Finding(BaseModel):
    """gstack식 리뷰 발견."""

    severity: Literal["blocker", "major", "minor", "nit"]
    confidence: Literal["high", "medium", "low", "very_low"] = "medium"
    path: str = ""
    line: int = 0
    category: str = ""
    summary: str
    fix: str = ""
    fingerprint: str = ""
    specialist: str = ""
    disposition: Literal["open", "fixed", "rejected", "deferred"] = "open"


class Verdict(BaseModel):
    """리뷰/보안/QA 공통 판정. decision은 단계별 허용값을 센서가 강제."""

    decision: Literal["approve", "request_changes", "pass", "fail", "needs_human"]
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    # 보안 단계 전용 보강 필드
    attack_surface: str = ""
    trust_boundary: str = ""
    evidence: str = ""


class QaVerdict(BaseModel):
    decision: Literal["pass", "fail", "needs_human"]
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)


class ExtractedEvent(BaseModel):
    """Gmail 일정 추출 후보."""

    title: str
    start_at: str            # ISO 8601
    end_at: str
    timezone: str = "Asia/Seoul"
    location: str = ""
    confidence: float = 0.0  # 0~1
    is_ambiguous: bool = False
    is_recurring: bool = False
    summary: str = ""


class RepoSummary(BaseModel):
    """skillRadar 저장소 1건의 3개 언어 요약 + 분류."""

    summary_en: str = ""
    summary_zh: str = ""
    summary_ko: str
    taxonomy: str = ""
    taxonomy_confidence: float = 0.0
    summary_status: str = "ok"   # ok|fallback|failed


class GenericOutput(BaseModel):
    """스키마 미지정 단계의 최소 출력."""

    result: str
    done_state: Literal["DONE", "DONE_WITH_CONCERNS", "BLOCKED", "NEEDS_CONTEXT"] = "DONE"
