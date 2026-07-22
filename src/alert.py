"""[보너스] 감정 변화 알림.

최근 N일의 부정 리뷰 비율을 그 직전 같은 길이의 기간과 비교해,
설정한 배수 이상으로 뛰면 경고한다.

'건수'가 아니라 '비율'로 비교하는 이유: 리뷰가 전체적으로 늘어난 것과
불만이 실제로 심해진 것은 다른 상황인데, 건수만 보면 구분되지 않는다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from src import config as config_module
from src.db import Database

logger = logging.getLogger(__name__)


def _negative_ratio(db: Database, date_from: str, date_to: str, **filters) -> tuple[float, int, int]:
    """구간의 (부정 비율, 부정 건수, 분석 건수)."""
    counts = db.sentiment_counts(date_from=date_from, date_to=date_to, **filters)
    analyzed = sum(counts.values())
    negative = counts.get("negative", 0)
    return ((negative / analyzed) if analyzed else 0.0), negative, analyzed


def detect_spike(
    db: Database, cfg: dict[str, Any],
    days: int | None = None, threshold: float | None = None, **filters,
) -> dict[str, Any] | None:
    """부정 리뷰 급증 여부를 판정한다. 데이터가 부족하면 None."""
    alert_cfg = cfg.get("alert", {})
    days = days or alert_cfg.get("days", 7)
    threshold = threshold or alert_cfg.get("threshold", 1.5)
    min_reviews = alert_cfg.get("min_reviews", 5)

    # '오늘'이 아니라 데이터의 마지막 날을 기준으로 삼는다.
    # 샘플·과거 데이터로 돌려도 의미 있는 비교가 되도록.
    _, last_date = db.date_range(**filters)
    if not last_date:
        return None

    try:
        anchor = datetime.strptime(last_date, "%Y-%m-%d")
    except ValueError:
        return None

    recent_from = (anchor - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    prev_to = (anchor - timedelta(days=days)).strftime("%Y-%m-%d")
    prev_from = (anchor - timedelta(days=days * 2 - 1)).strftime("%Y-%m-%d")

    recent_ratio, recent_neg, recent_total = _negative_ratio(db, recent_from, last_date, **filters)
    prev_ratio, prev_neg, prev_total = _negative_ratio(db, prev_from, prev_to, **filters)

    result = {
        "days": days,
        "threshold": threshold,
        "anchor": last_date,
        "recent_period": (recent_from, last_date),
        "prev_period": (prev_from, prev_to),
        "recent_ratio": recent_ratio, "recent_neg": recent_neg, "recent_total": recent_total,
        "prev_ratio": prev_ratio, "prev_neg": prev_neg, "prev_total": prev_total,
        "ratio_change": (recent_ratio / prev_ratio) if prev_ratio else None,
        "triggered": False,
        "reason": "",
    }

    if recent_total < min_reviews or prev_total < min_reviews:
        result["reason"] = (
            f"비교에 필요한 최소 리뷰 수({min_reviews}건)를 채우지 못했습니다 "
            f"(최근 {recent_total}건 / 직전 {prev_total}건)."
        )
        return result

    if prev_ratio == 0:
        # 직전 기간에 부정이 하나도 없었다면 배수 계산이 불가능하다.
        # 이때는 최근 부정 비율 자체가 높은지로 판정한다.
        result["triggered"] = recent_ratio >= 0.3
        result["reason"] = "직전 기간에 부정 리뷰가 없어 최근 비율(30% 기준)로 판정했습니다."
        return result

    result["triggered"] = (recent_ratio / prev_ratio) >= threshold
    result["reason"] = (
        f"최근 부정 비율이 직전 기간의 {recent_ratio / prev_ratio:.2f}배입니다 "
        f"(임계 {threshold}배)."
    )
    return result


def format_alert(result: dict[str, Any] | None) -> list[str]:
    """리포트·콘솔에서 함께 쓰는 출력 문자열."""
    if result is None:
        return ["- 날짜 정보가 있는 분석 결과가 없어 감정 변화를 판정할 수 없습니다."]

    recent_from, recent_to = result["recent_period"]
    prev_from, prev_to = result["prev_period"]

    lines = [
        f"- 기준일: {result['anchor']} (데이터의 마지막 작성일)",
        f"- 최근 {result['days']}일 ({recent_from} ~ {recent_to}): "
        f"부정 {result['recent_neg']}/{result['recent_total']}건 = {result['recent_ratio'] * 100:.1f}%",
        f"- 직전 {result['days']}일 ({prev_from} ~ {prev_to}): "
        f"부정 {result['prev_neg']}/{result['prev_total']}건 = {result['prev_ratio'] * 100:.1f}%",
    ]

    if result["triggered"]:
        change = result["ratio_change"]
        lines.append(
            f"- **경고: 부정 리뷰 비율이 급증했습니다"
            + (f" ({change:.2f}배)" if change else "")
            + "** — 즉시 원인 점검이 필요합니다."
        )
    else:
        lines.append(f"- 정상 범위입니다. {result['reason']}")

    return lines


# --------------------------------------------------------------------- CLI

def cmd_alert(args, cfg: dict[str, Any]) -> int:
    filters: dict[str, Any] = {}
    if getattr(args, "product", None):
        filters["product"] = args.product

    db_path = config_module.resolve_path(cfg["paths"]["db"])
    with Database(db_path) as db:
        result = detect_spike(db, cfg, days=args.days, threshold=args.threshold, **filters)

    print()
    print("=== 감정 변화 알림 ===")
    if filters:
        print(f"조건: {', '.join(f'{k}={v}' for k, v in filters.items())}")
    for line in format_alert(result):
        print(line.replace("**", ""))
    print()

    if result and result["triggered"]:
        logger.warning("부정 리뷰 비율 급증이 감지되었습니다.")
        return 0

    logger.info("부정 리뷰 급증은 감지되지 않았습니다.")
    return 0
