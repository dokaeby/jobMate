"""output_schema 이름 → Pydantic 클래스 레지스트리."""

from __future__ import annotations

from pydantic import BaseModel

from ..models import (
    AnalysisResult,
    ChangeProposal,
    ExtractedEvent,
    GenericOutput,
    QaVerdict,
    RepoSummary,
    Verdict,
)

SCHEMAS: dict[str, type[BaseModel]] = {
    "AnalysisResult": AnalysisResult,
    "ChangeProposal": ChangeProposal,
    "Verdict": Verdict,
    "QaVerdict": QaVerdict,
    "ExtractedEvent": ExtractedEvent,
    "RepoSummary": RepoSummary,
    "GenericOutput": GenericOutput,
}
