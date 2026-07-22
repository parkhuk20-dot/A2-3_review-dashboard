"""AI 감정 분석: 리뷰별 감정(긍정/부정/중립) + 신뢰도 점수(0.0~1.0).

- 대상 선택: --all / --id / --unanalyzed(기본)
- 이미 분석된 리뷰는 기본 스킵(재분석은 --all)
- API 실패는 로깅 후 스킵하고 다음 리뷰로 넘어간다
- [보너스] 한국어·영어 리뷰를 모두 처리한다
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src import config as config_module
from src.ai.client import AIClient, AIError
from src.db import Database

logger = logging.getLogger(__name__)

VALID_SENTIMENTS = ("positive", "negative", "neutral")

# 모델이 한국어 라벨이나 대문자로 답해도 하나로 모은다.
_LABEL_ALIASES = {
    "positive": "positive", "pos": "positive", "긍정": "positive", "긍정적": "positive",
    "negative": "negative", "neg": "negative", "부정": "negative", "부정적": "negative",
    "neutral": "neutral", "neu": "neutral", "중립": "neutral", "중립적": "neutral",
    "mixed": "neutral",
}

SYSTEM_PROMPT = (
    "당신은 고객 리뷰 감정 분석 전문가입니다. "
    "한국어와 영어 리뷰를 모두 처리할 수 있습니다. "
    "리뷰의 감정을 positive / negative / neutral 중 하나로 분류하고, "
    "판단 신뢰도를 0.0~1.0 사이 숫자로 매기며, 감정을 드러내는 핵심 키워드를 뽑습니다. "
    "반드시 다음 JSON 형식으로만 답하세요: "
    '{"sentiment": "positive|negative|neutral", "score": 0.0, "keywords": ["키워드1", "키워드2"]}'
)

# mock 모드에서 쓰는 최소한의 감정어 사전 (API 없이 전체 흐름을 검증하기 위한 용도)
_POSITIVE_WORDS = [
    "좋", "만족", "훌륭", "완벽", "빠르", "빨랐", "편하", "편리", "추천", "최고",
    "깔끔", "정확", "튼튼", "가성비", "감사", "재구매", "amazing", "excellent",
    "great", "perfect", "love", "sturdy", "easy",
]
_NEGATIVE_WORDS = [
    "불량", "지연", "늦", "최악", "실망", "아쉽", "불친절", "안 됩", "안돼", "고장",
    "환불", "교환", "답답", "엉망", "스크래치", "헐거", "샙니다", "방전", "끊",
    "frustrating", "never", "defective", "zero communication", "poor",
]


def normalize_label(value: Any) -> str | None:
    """모델이 준 감정 라벨을 내부 표준 라벨로 정규화한다."""
    if value is None:
        return None
    key = str(value).strip().lower()
    return _LABEL_ALIASES.get(key)


def normalize_score(value: Any) -> float:
    """신뢰도 점수를 0.0~1.0 으로 다듬는다."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.5
    if score > 1.0:  # 0~100 척도로 답하는 경우 보정
        score = score / 100.0 if score <= 100 else 1.0
    return round(min(max(score, 0.0), 1.0), 3)


def mock_sentiment(text: str, rating: int | None) -> dict[str, Any]:
    """API 없이 감정을 추정한다.

    별점이 있으면 별점을 우선 신뢰하고, 없으면 감정어 사전으로 판단한다.
    실제 분석을 대체하려는 것이 아니라 파이프라인 검증용이다.
    """
    lowered = text.lower()
    positive_hits = sum(1 for w in _POSITIVE_WORDS if w in lowered)
    negative_hits = sum(1 for w in _NEGATIVE_WORDS if w in lowered)

    if rating is not None:
        if rating >= 4:
            label, base = "positive", 0.75
        elif rating <= 2:
            label, base = "negative", 0.75
        else:
            label, base = "neutral", 0.6
        # 본문이 별점과 같은 방향이면 신뢰도를 조금 올린다.
        agreement = positive_hits if label == "positive" else negative_hits
        score = min(0.95, base + 0.05 * agreement)
    elif positive_hits > negative_hits:
        label, score = "positive", min(0.9, 0.55 + 0.05 * positive_hits)
    elif negative_hits > positive_hits:
        label, score = "negative", min(0.9, 0.55 + 0.05 * negative_hits)
    else:
        label, score = "neutral", 0.5

    keywords = [w for w in (_POSITIVE_WORDS if label == "positive" else _NEGATIVE_WORDS)
                if w in lowered][:3]
    return {"sentiment": label, "score": round(score, 3), "keywords": keywords}


def analyze_one(client: AIClient, text: str, rating: int | None, lang: str | None) -> dict[str, Any]:
    """리뷰 1건을 분석한다. 실패하면 AIError 를 올린다."""
    if client.mock:
        return mock_sentiment(text, rating)

    lang_hint = "이 리뷰는 영어로 작성되었습니다." if lang == "en" else ""
    rating_hint = f"작성자가 매긴 별점은 {rating}점(5점 만점)입니다." if rating is not None else ""
    user_prompt = (
        f"{lang_hint}\n{rating_hint}\n"
        "아래 고객 리뷰의 감정을 분석하세요. "
        "별점은 참고만 하고, 본문 내용을 우선해서 판단하세요.\n\n"
        f"리뷰: {text}"
    ).strip()

    data = client.complete_json(SYSTEM_PROMPT, user_prompt)

    label = normalize_label(data.get("sentiment"))
    if label is None:
        raise AIError(f"알 수 없는 감정 라벨입니다: {data.get('sentiment')!r}")

    keywords = data.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in re.split(r"[,;/]", keywords) if k.strip()]

    return {
        "sentiment": label,
        "score": normalize_score(data.get("score")),
        "keywords": [str(k).strip() for k in keywords if str(k).strip()][:5],
    }


def analyze_reviews(
    db: Database, cfg: dict[str, Any], mock: bool = False,
    mode: str = "unanalyzed", review_id: int | None = None,
    limit: int | None = None, **filters,
) -> dict[str, int]:
    """대상 리뷰를 골라 감정 분석하고 결과를 저장한다."""
    client = AIClient(cfg, mock=mock)
    targets = db.fetch_for_analysis(mode=mode, review_id=review_id, limit=limit, **filters)

    if not targets:
        logger.info("분석할 리뷰가 없습니다. (이미 모두 분석되었거나 조건에 맞는 리뷰가 없음)")
        return {"target": 0, "success": 0, "failed": 0, "skipped": 0}

    logger.info("분석 대상: %d건 (모델=%s)", len(targets), client.model_name)

    success = failed = skipped = 0
    total = len(targets)

    for index, row in enumerate(targets, start=1):
        # 기본(unanalyzed) 모드에서만 기분석 건을 건너뛴다.
        # --all / --id 는 사용자가 재분석을 명시한 것이므로 다시 호출한다.
        if mode == "unanalyzed" and row["sentiment"] is not None:
            skipped += 1
            logger.debug("[%d/%d] ID=%s 이미 분석됨 — 건너뜀", index, total, row["id"])
            continue

        try:
            result = analyze_one(client, row["review_text"], row["rating"], row["lang"])
        except AIError as exc:
            failed += 1
            logger.error("[%d/%d] ID=%s 분석 실패 — 건너뜁니다: %s", index, total, row["id"], exc)
            continue

        db.save_sentiment(
            review_id=row["id"],
            sentiment=result["sentiment"],
            score=result["score"],
            keywords=", ".join(result["keywords"]) if result["keywords"] else None,
            model=client.model_name,
        )
        success += 1
        logger.info(
            "[%d/%d] ID=%s 분석 완료: %s (%.2f)",
            index, total, row["id"], result["sentiment"], result["score"],
        )

    return {"target": total, "success": success, "failed": failed, "skipped": skipped}


# --------------------------------------------------------------------- CLI

def cmd_analyze(args, cfg: dict[str, Any]) -> int:
    if args.id is not None:
        mode = "id"
    elif args.all:
        mode = "all"
    else:
        mode = "unanalyzed"  # 기본값

    filters: dict[str, Any] = {}
    if getattr(args, "product", None):
        filters["product"] = args.product
    if getattr(args, "lang", None):
        filters["lang"] = args.lang

    db_path = config_module.resolve_path(cfg["paths"]["db"])
    try:
        with Database(db_path) as db:
            stats = analyze_reviews(
                db, cfg, mock=args.mock, mode=mode,
                review_id=args.id, limit=args.limit, **filters,
            )
    except AIError as exc:
        logger.error("%s", exc)
        return 2

    if stats["target"]:
        logger.info(
            "분석 완료: %d건 성공, %d건 실패%s",
            stats["success"], stats["failed"],
            f", {stats['skipped']}건 스킵(기분석)" if stats["skipped"] else "",
        )
        logger.info("다음 단계: python main.py extract")
    return 0
