"""예산/루프 상한 트래커 — 소진 시 안전 정지."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class BudgetTracker:
    max_tokens: int = 200_000
    max_tool_calls: int = 20
    max_seconds: float = 600.0
    max_cost_usd: float = 5.0
    # 루프 상한
    max_review_rounds: int = 2
    max_implement_rounds: int = 3
    max_fix_rounds: int = 5

    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    review_rounds: int = 0
    implement_rounds: int = 0
    fix_rounds: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def add_usage(self, tokens_in: int, tokens_out: int, cost: float = 0.0) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost

    def add_tool_call(self) -> None:
        self.tool_calls += 1

    def exhausted_reason(self) -> str | None:
        if self.tokens_in + self.tokens_out >= self.max_tokens:
            return "token_budget"
        if self.tool_calls >= self.max_tool_calls:
            return "tool_call_budget"
        if time.monotonic() - self.started_at >= self.max_seconds:
            return "time_budget"
        if self.cost_usd >= self.max_cost_usd:
            return "cost_budget"
        return None

    def can_review(self) -> bool:
        return self.review_rounds < self.max_review_rounds

    def can_implement(self) -> bool:
        return self.implement_rounds < self.max_implement_rounds

    def can_fix(self) -> bool:
        return self.fix_rounds < self.max_fix_rounds

    def increment_review(self) -> None:
        self.review_rounds += 1

    def increment_implement(self) -> None:
        self.implement_rounds += 1

    def increment_fix(self) -> None:
        self.fix_rounds += 1
