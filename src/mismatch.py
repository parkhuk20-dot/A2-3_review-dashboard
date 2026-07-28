"""별점과 본문 감정이 어긋나는 리뷰를 찾아낸다.

일치율이 90%라는 건 뒤집어 보면 **10%는 별점만 봐서는 못 잡는다**는 뜻이다.
별점 평균은 이 10%를 통째로 삼켜버리므로, 여기서 따로 끄집어낸다.

두 방향을 구분한다.
    숨은 불만  : 별점은 후한데 본문은 부정적 — 놓치기 쉬운 실질 불만
    관대한 본문: 별점은 낮은데 본문은 우호적 — 오입력이거나 특정 항목만 불만
"""

from __future__ import annotations

import logging
from typing import Any

from src import config as config_module
from src.db import Database
from src.viewer import SENTIMENT_KO, pad, stars, truncate

logger = logging.getLogger(__name__)

# 감정을 -1 ~ +1 축에 올려 별점 기대값과 산술 비교한다.
SENTIMENT_SCORE = {"negative": -1, "neutral": 0, "positive": 1}

HIDDEN_COMPLAINT = "숨은 불만"
GENEROUS_TEXT = "관대한 본문"


def expected_sentiment(rating: int) -> str:
    """별점으로부터 기대되는 감정. 리포트의 일치율 기준과 같아야 한다."""
    if rating >= 4:
        return "positive"
    if rating <= 2:
        return "negative"
    return "neutral"


def find_mismatches(db: Database, min_gap: int = 1, kind: str = "all", **filters) -> dict[str, Any]:
    """불일치 리뷰와 별점별 분포를 함께 계산한다."""
    rows = db.query_reviews(analyzed=True, sort="rating", order="desc", **filters)

    comparable: list[Any] = []
    mismatches: list[dict[str, Any]] = []
    # 별점별 (비교 가능, 불일치) 건수 — ★3에 몰리는지 같은 패턴을 보기 위함
    per_rating: dict[int, list[int]] = {}

    for row in rows:
        if row["rating"] is None or not row["sentiment"]:
            continue

        comparable.append(row)
        rating = int(row["rating"])
        per_rating.setdefault(rating, [0, 0])
        per_rating[rating][0] += 1

        expected = expected_sentiment(rating)
        if expected == row["sentiment"]:
            continue

        per_rating[rating][1] += 1
        gap = SENTIMENT_SCORE[row["sentiment"]] - SENTIMENT_SCORE[expected]
        label = HIDDEN_COMPLAINT if gap < 0 else GENEROUS_TEXT

        if abs(gap) < min_gap:
            continue
        if kind == "hidden" and label != HIDDEN_COMPLAINT:
            continue
        if kind == "generous" and label != GENEROUS_TEXT:
            continue

        mismatches.append({
            "row": row, "rating": rating, "expected": expected,
            "actual": row["sentiment"], "gap": gap, "label": label,
        })

    # 어긋난 폭이 큰 순서로. 같은 폭이면 신뢰도가 높은 쪽이 더 확실한 사례다.
    mismatches.sort(key=lambda m: (-abs(m["gap"]), -(m["row"]["score"] or 0)))

    return {
        "comparable": len(comparable),
        "mismatches": mismatches,
        "per_rating": per_rating,
        "hidden": sum(1 for m in mismatches if m["label"] == HIDDEN_COMPLAINT),
        "generous": sum(1 for m in mismatches if m["label"] == GENEROUS_TEXT),
    }


def worst_rating_bucket(per_rating: dict[int, list[int]], min_base: int = 3) -> tuple[int, float] | None:
    """불일치율이 가장 높은 별점 구간. 표본이 너무 작은 구간은 제외한다."""
    candidates = [
        (rating, mismatched / total * 100)
        for rating, (total, mismatched) in per_rating.items()
        if total >= min_base and mismatched
    ]
    return max(candidates, key=lambda x: x[1]) if candidates else None


def build_lines(result: dict[str, Any], top_n: int = 10) -> list[str]:
    """콘솔·리포트가 공유하는 출력 문자열."""
    comparable = result["comparable"]
    mismatches = result["mismatches"]

    if comparable == 0:
        return ["- 별점과 감정 분석이 모두 있는 리뷰가 없어 비교할 수 없습니다."]

    total_mismatch = result["hidden"] + result["generous"]
    lines = [
        f"- 비교 가능 {comparable}건 중 불일치 {total_mismatch}건 "
        f"({total_mismatch / comparable * 100:.1f}%)",
        f"- {HIDDEN_COMPLAINT}(별점보다 본문이 부정적): {result['hidden']}건",
        f"- {GENEROUS_TEXT}(별점보다 본문이 긍정적): {result['generous']}건",
    ]

    if not mismatches:
        lines.append("- 조건에 맞는 불일치 리뷰가 없습니다.")
        return lines

    lines.append("")
    lines.append("[별점별 불일치율]")
    worst = worst_rating_bucket(result["per_rating"])
    for rating in sorted(result["per_rating"], reverse=True):
        total, mismatched = result["per_rating"][rating]
        marker = "  ← 가장 높음" if worst and rating == worst[0] and mismatched else ""
        lines.append(
            f"{stars(rating)} : {mismatched}/{total}건 ({mismatched / total * 100:.1f}%){marker}"
        )

    lines.append("")
    lines.append(f"[불일치 리뷰 (상위 {min(top_n, len(mismatches))}건)]")
    for item in mismatches[:top_n]:
        row = item["row"]
        score = f"{row['score']:.2f}" if row["score"] is not None else "-.--"
        lines.append(
            f"[{row['id']:>3}] {stars(item['rating'])} → "
            f"{pad(SENTIMENT_KO[item['actual']], 4)} ({score}) | "
            f"{pad(item['label'], 12)} | {truncate(row['review_text'], 44)}"
        )

    if worst:
        rating, rate = worst
        lines.append("")
        lines.append("[해석]")
        lines.append(
            f"★{rating} 리뷰의 {rate:.1f}%가 별점에서 기대되는 감정과 다릅니다. "
            "별점 평균만 보면 이 리뷰들의 실제 온도가 그대로 묻힙니다."
        )
        if result["hidden"] > result["generous"]:
            lines.append(
                f"특히 {HIDDEN_COMPLAINT}이 {result['hidden']}건으로 더 많습니다 — "
                "별점은 후하게 주면서 본문으로만 불만을 남기는 패턴이라, "
                "별점 지표만 추적하면 놓치게 됩니다."
            )

    return lines


# --------------------------------------------------------------------- CLI

def cmd_mismatch(args, cfg: dict[str, Any]) -> int:
    filters: dict[str, Any] = {}
    for attr in ("product", "date_from", "date_to"):
        value = getattr(args, attr, None)
        if value:
            filters[attr] = value

    db_path = config_module.resolve_path(cfg["paths"]["db"])
    with Database(db_path) as db:
        result = find_mismatches(db, min_gap=args.min_gap, kind=args.kind, **filters)

    print()
    print("=== 별점–감정 불일치 분석 ===")
    if filters:
        print(f"조건: {', '.join(f'{k}={v}' for k, v in filters.items())}")
    for line in build_lines(result, top_n=args.limit):
        print(line)
    print()

    logger.info("불일치 분석 완료: %d건", len(result["mismatches"]))
    return 0
