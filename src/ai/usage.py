"""API 토큰 사용량 · 비용 추적.

호출할 때마다 응답의 `usage` 를 모아 두었다가, 명령이 끝나면 한 번에 기록한다.
단가는 모델마다 다르고 수시로 바뀌므로 **설정 파일에서 읽는다**.
모르는 모델이면 비용을 0 으로 꾸며내지 않고 `None`(가격 정보 없음)으로 둔다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 100만 토큰당 USD. 공급자 가격표가 바뀔 수 있으니 config.json 에서 덮어쓸 수 있다.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}


class UsageTracker:
    """한 번의 명령 실행 동안의 토큰 사용량을 모은다.

    병렬 호출에서 여러 스레드가 동시에 더하므로 락으로 보호한다.
    """

    def __init__(self, model: str, pricing: dict[str, Any] | None = None):
        self.model = model
        # config.json 에 섞인 `_comment` 같은 주석 키는 단가표에서 걸러낸다.
        overrides = {
            name: rates for name, rates in (pricing or {}).items()
            if not name.startswith("_") and isinstance(rates, dict)
        }
        self.pricing = {**DEFAULT_PRICING, **overrides}
        self._lock = threading.Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += int(prompt_tokens or 0)
            self.completion_tokens += int(completion_tokens or 0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_usd(self) -> float | None:
        """예상 비용(USD). 단가를 모르는 모델이면 None."""
        rates = self.pricing.get(self.model)
        if not rates:
            return None
        return (
            self.prompt_tokens / 1_000_000 * rates.get("input", 0.0)
            + self.completion_tokens / 1_000_000 * rates.get("output", 0.0)
        )

    def as_record(self, command: str) -> dict[str, Any]:
        return {
            "command": command,
            "model": self.model,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
        }

    def summary(self) -> str:
        """실행 직후 로그로 남길 한 줄 요약."""
        if not self.calls:
            return "API 호출 없음"

        cost = self.cost_usd
        cost_text = f"약 ${cost:.4f}" if cost is not None else "가격 정보 없음"
        return (
            f"API 사용량: {self.calls}회 호출, "
            f"토큰 {self.total_tokens:,}개"
            f"(입력 {self.prompt_tokens:,} / 출력 {self.completion_tokens:,}), {cost_text}"
        )


def format_cost(value: float | None) -> str:
    """비용을 사람이 읽는 형태로. 아주 작은 값이 0 으로 보이지 않게 자릿수를 맞춘다."""
    if value is None:
        return "-"
    if value == 0:
        return "$0"
    return f"${value:.4f}" if value < 1 else f"${value:.2f}"


def build_lines(totals: dict[str, Any], by_command: list[dict[str, Any]]) -> list[str]:
    """누적 사용량 출력. 콘솔과 대시보드 리포트가 공유한다."""
    if not totals.get("calls"):
        return ["- 기록된 API 사용량이 없습니다. (아직 실호출을 하지 않았거나 `--mock` 으로만 실행)"]

    lines = [
        f"- 총 호출: {totals['calls']:,}회 ({totals['runs']}번의 실행)",
        f"- 총 토큰: {totals['total_tokens']:,}개 "
        f"(입력 {totals['prompt_tokens']:,} / 출력 {totals['completion_tokens']:,})",
        f"- 누적 비용: {format_cost(totals.get('cost_usd'))}",
    ]

    if by_command:
        lines.append("")
        lines.append("[명령별]")
        for item in by_command:
            lines.append(
                f"- {item['command']} ({item['model']}): "
                f"{item['calls']:,}회 / 토큰 "
                f"{(item['prompt_tokens'] or 0) + (item['completion_tokens'] or 0):,}개 / "
                f"{format_cost(item.get('cost_usd'))}"
            )

    return lines


# --------------------------------------------------------------------- CLI

def cmd_usage(args, cfg: dict[str, Any]) -> int:
    """지금까지 쓴 API 토큰과 비용을 보여준다."""
    from src import config as config_module
    from src.db import Database

    db_path = config_module.resolve_path(cfg["paths"]["db"])
    with Database(db_path) as db:
        totals = db.usage_totals()
        by_command = db.usage_by_command()
        recent = db.recent_usage(limit=args.limit)

    print()
    print("=== API 사용량 ===")
    for line in build_lines(totals, by_command):
        print(line)

    if recent:
        print()
        print(f"[최근 실행 {len(recent)}건]")
        for item in recent:
            tokens = (item["prompt_tokens"] or 0) + (item["completion_tokens"] or 0)
            print(f"- {item['created_at']} | {item['command']:9s} | "
                  f"{item['calls']:>4}회 | 토큰 {tokens:>7,} | "
                  f"{format_cost(item['cost_usd'])}")

    print()
    if totals.get("calls"):
        print("* 비용은 config.json 의 단가표로 계산한 추정치입니다. "
              "실제 청구액은 공급자 대시보드를 확인하세요.")
        print()
    return 0
