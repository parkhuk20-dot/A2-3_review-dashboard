"""AI 키워드·요약 추출.

조건(기간·감정·제품)에 맞는 리뷰를 **한 번의 호출로 묶어** 분석한다.
리뷰별로 호출하면 비용·시간이 선형으로 늘고, 무엇보다 "전체를 관통하는 불만 유형"
같은 건 개별 리뷰만 봐서는 나오지 않기 때문이다.

추출 항목(요구 2개 이상 → 4개):
    긍정 키워드 / 부정 키워드 / 전체 요약 / 불만·칭찬 유형 / 개선 제안
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

from src import config as config_module
from src.ai.client import AIClient, AIError
from src.db import Database

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "당신은 고객 리뷰를 분석해 비즈니스 인사이트를 도출하는 데이터 분석가입니다. "
    "한국어와 영어 리뷰가 섞여 있어도 모두 이해하고, 결과는 한국어로 작성합니다. "
    "여러 리뷰를 종합해 반복되는 패턴을 찾아내고, 실행 가능한 개선 제안을 제시합니다.\n"
    "반드시 다음 JSON 형식으로만 답하세요:\n"
    "{\n"
    '  "positive_keywords": ["칭찬 키워드", "..."],\n'
    '  "negative_keywords": ["불만 키워드", "..."],\n'
    '  "summary": "전체 리뷰를 3~5문장으로 요약",\n'
    '  "complaint_types": [{"type": "유형명", "count": 0, "detail": "설명"}],\n'
    '  "suggestions": ["개선 제안", "..."]\n'
    "}\n"
    "키워드는 각각 5~8개, complaint_types 는 3~5개, suggestions 는 3~5개로 하세요."
)


def _as_list(value: Any, limit: int = 10) -> list[str]:
    """모델이 리스트 대신 문자열로 답해도 리스트로 만든다."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [v.strip() for v in re.split(r"[,;\n]", value) if v.strip()]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def _complaint_types_to_text(value: Any) -> str:
    """불만 유형을 '1. 배송 관련 (9건): 설명' 형태의 여러 줄 텍스트로 만든다."""
    if not value:
        return ""
    if isinstance(value, str):
        return value

    lines: list[str] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, dict):
            name = item.get("type") or item.get("name") or "기타"
            count = item.get("count")
            detail = item.get("detail") or item.get("description") or ""
            head = f"{index}. {name}" + (f" ({count}건)" if count else "")
            lines.append(f"{head}: {detail}" if detail else head)
        else:
            lines.append(f"{index}. {item}")
    return "\n".join(lines)


def mock_extract(rows: list, top_n: int = 8) -> dict[str, Any]:
    """API 없이 빈도 기반으로 추출 결과를 만든다(파이프라인 검증용)."""
    positive_texts = [r["review_text"] for r in rows if r["sentiment"] == "positive"]
    negative_texts = [r["review_text"] for r in rows if r["sentiment"] == "negative"]

    def top_words(texts: list[str]) -> list[str]:
        # 조사·불용어를 완전히 처리하진 않는다. mock 은 형식 검증이 목적이다.
        words = Counter()
        for text in texts:
            for token in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text):
                if token not in ("합니다", "습니다", "있습니다", "같습니다", "제품", "리뷰"):
                    words[token] += 1
        return [w for w, _ in words.most_common(top_n)]

    # 저장된 리뷰별 키워드도 함께 모아 신뢰도를 조금 높인다.
    keyword_pool = Counter()
    for row in rows:
        if row["keywords"]:
            for kw in str(row["keywords"]).split(","):
                if kw.strip():
                    keyword_pool[kw.strip()] += 1

    return {
        "positive_keywords": top_words(positive_texts),
        "negative_keywords": top_words(negative_texts),
        "summary": (
            f"[mock] 총 {len(rows)}건의 리뷰를 집계했습니다. "
            f"긍정 {len(positive_texts)}건, 부정 {len(negative_texts)}건으로 "
            f"{'긍정' if len(positive_texts) >= len(negative_texts) else '부정'} 의견이 우세합니다. "
            "실제 요약 문장은 AI 호출 시 생성됩니다."
        ),
        "complaint_types": [
            {"type": "빈출 키워드 기반 분류", "count": len(negative_texts),
             "detail": ", ".join(top_words(negative_texts)[:5])},
        ],
        "suggestions": [
            "mock 모드에서는 제안이 생성되지 않습니다. --mock 없이 실행하세요.",
        ],
    }


def extract_insights(
    db: Database, cfg: dict[str, Any], mock: bool = False,
    sentiment: str | None = None, product: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    limit: int | None = None,
) -> dict[str, Any] | None:
    """조건에 맞는 리뷰를 모아 AI 추출을 수행하고 저장한다."""
    ai_cfg = cfg.get("ai", {})
    max_reviews = limit or ai_cfg.get("max_reviews_per_extraction", 80)
    body_chars = ai_cfg.get("review_chars_for_extraction", 300)

    filters: dict[str, Any] = {"analyzed": True}
    if sentiment:
        filters["sentiment"] = sentiment
    if product:
        filters["product"] = product
    if date_from:
        filters["date_from"] = date_from
    if date_to:
        filters["date_to"] = date_to

    rows = db.query_reviews(limit=max_reviews, sort="date", order="desc", **filters)
    if not rows:
        logger.warning("조건에 맞는 리뷰가 없습니다. 먼저 analyze 를 실행했는지 확인하세요.")
        return None

    scope_label = sentiment or "전체"
    logger.info("추출 대상: %s 리뷰 %d건", scope_label, len(rows))

    client = AIClient(cfg, mock=mock)

    if mock:
        data = mock_extract(rows, top_n=cfg.get("report", {}).get("top_n", 5) + 3)
    else:
        logger.info("AI 분석 요청 중... (모델=%s)", client.model_name)
        # 리뷰마다 별점·감정을 함께 붙여 모델이 맥락을 잡을 수 있게 한다.
        lines = []
        for row in rows:
            rating = f"★{row['rating']}" if row["rating"] else "★-"
            date = row["review_date"] or "날짜없음"
            body = (row["review_text"] or "")[:body_chars]
            lines.append(f"- [{row['id']}] {rating} | {date} | {row['sentiment']} | {body}")

        user_prompt = (
            f"다음은 분석 대상 고객 리뷰 {len(rows)}건입니다"
            + (f" (감정: {sentiment})" if sentiment else "")
            + (f" (제품: {product})" if product else "")
            + ".\n\n"
            + "\n".join(lines)
            + "\n\n이 리뷰들을 종합해 지정된 JSON 형식으로 분석 결과를 작성하세요."
        )
        data = client.complete_json(SYSTEM_PROMPT, user_prompt)

    top_n_keywords = cfg.get("report", {}).get("top_n", 5) + 3
    record = {
        "scope_sentiment": sentiment,
        "scope_product": product,
        "date_from": date_from,
        "date_to": date_to,
        "n_reviews": len(rows),
        "pos_keywords": ", ".join(_as_list(data.get("positive_keywords"), top_n_keywords)),
        "neg_keywords": ", ".join(_as_list(data.get("negative_keywords"), top_n_keywords)),
        "summary": str(data.get("summary") or "").strip(),
        "complaint_types": _complaint_types_to_text(data.get("complaint_types")),
        "suggestions": "\n".join(f"- {s}" for s in _as_list(data.get("suggestions"), 6)),
        "model": client.model_name,
    }

    extraction_id = db.save_extraction(record)
    record["id"] = extraction_id
    logger.info("추출 완료 (extraction #%d)", extraction_id)
    return record


def print_extraction(record: dict[str, Any], scope_label: str) -> None:
    """추출 결과를 콘솔에 보기 좋게 출력한다."""
    print()
    print(f"=== {scope_label} 리뷰 키워드 분석 ===")

    if record["pos_keywords"]:
        print("\n[주요 칭찬 키워드]")
        print(record["pos_keywords"])
    if record["neg_keywords"]:
        print("\n[주요 불만 키워드]")
        print(record["neg_keywords"])
    if record["complaint_types"]:
        print("\n[주요 유형 분류]")
        print(record["complaint_types"])
    if record["summary"]:
        print("\n[전체 요약]")
        print(record["summary"])
    if record["suggestions"]:
        print("\n[개선 제안]")
        print(record["suggestions"])
    print()


# --------------------------------------------------------------------- CLI

def cmd_extract(args, cfg: dict[str, Any]) -> int:
    db_path = config_module.resolve_path(cfg["paths"]["db"])

    try:
        with Database(db_path) as db:
            record = extract_insights(
                db, cfg, mock=args.mock,
                sentiment=args.sentiment, product=args.product,
                date_from=args.date_from, date_to=args.date_to, limit=args.limit,
            )
    except AIError as exc:
        logger.error("%s", exc)
        return 2

    if record is None:
        return 1

    label_map = {"positive": "긍정", "negative": "부정", "neutral": "중립"}
    scope = label_map.get(args.sentiment or "", "전체")
    if args.product:
        scope += f" · {args.product}"

    print_extraction(record, scope)
    logger.info("다음 단계: python main.py dashboard")
    return 0
