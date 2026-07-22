"""데이터 조회: list(목록) / show(상세) / stats(통계).

콘솔 출력이라 한글·영문 폭이 섞이면 표가 어긋난다. 동아시아 문자 폭을 세어
자르고 채우는 헬퍼를 두어 정렬을 맞춘다.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from typing import Any

from src import config as config_module
from src.db import Database

logger = logging.getLogger(__name__)

SENTIMENT_KO = {"positive": "긍정", "negative": "부정", "neutral": "중립"}


def display_width(text: str) -> int:
    """터미널에서 차지하는 칸 수. 한글·전각은 2칸으로 센다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def truncate(text: str, width: int) -> str:
    """표시 폭 기준으로 자른다. 잘리면 말줄임표를 붙인다."""
    text = (text or "").replace("\n", " ")
    if display_width(text) <= width:
        return text

    result = ""
    used = 0
    for ch in text:
        char_width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + char_width > width - 3:
            break
        result += ch
        used += char_width
    return result + "..."


def pad(text: str, width: int) -> str:
    """표시 폭 기준 좌측 정렬 패딩."""
    return text + " " * max(0, width - display_width(text))


def stars(rating: int | None) -> str:
    """별점을 ★☆ 로 표시. 없으면 빈 칸으로 폭을 맞춘다."""
    if rating is None:
        return "  -  "
    return "★" * rating + "☆" * (5 - rating)


def format_sentiment(row) -> str:
    """감정 + 신뢰도. 미분석이면 표시를 달리한다."""
    if not row["sentiment"]:
        return "미분석"
    score = f" ({row['score']:.2f})" if row["score"] is not None else ""
    return f"{row['sentiment']}{score}"


def _collect_filters(args) -> dict[str, Any]:
    """CLI 인자에서 조회 필터만 뽑아낸다."""
    mapping = {
        "sentiment": "sentiment", "rating": "rating",
        "rating_min": "rating_min", "rating_max": "rating_max",
        "date_from": "date_from", "date_to": "date_to",
        "product": "product", "category": "category",
        "lang": "lang", "keyword": "keyword",
    }
    filters: dict[str, Any] = {}
    for attr, key in mapping.items():
        value = getattr(args, attr, None)
        if value is not None:
            filters[key] = value
    return filters


def _describe_filters(filters: dict[str, Any]) -> str:
    """헤더에 보여줄 필터 설명 문자열."""
    labels = {
        "sentiment": "감정", "rating": "별점", "rating_min": "별점≥", "rating_max": "별점≤",
        "date_from": "시작일", "date_to": "종료일", "product": "제품",
        "category": "카테고리", "lang": "언어", "keyword": "키워드",
    }
    parts = [f"{labels.get(k, k)}: {v}" for k, v in filters.items()]
    return ", ".join(parts) if parts else "전체"


# --------------------------------------------------------------------- list

def cmd_list(args, cfg: dict[str, Any]) -> int:
    filters = _collect_filters(args)
    page = max(1, args.page)
    size = max(1, args.size)

    db_path = config_module.resolve_path(cfg["paths"]["db"])
    with Database(db_path) as db:
        total = db.count_reviews(**filters)
        if total == 0:
            print(f"\n조건에 맞는 리뷰가 없습니다. ({_describe_filters(filters)})\n")
            return 0

        total_pages = (total + size - 1) // size
        page = min(page, total_pages)  # 마지막 페이지를 넘어가면 마지막으로 보정
        rows = db.query_reviews(
            limit=size, offset=(page - 1) * size,
            sort=args.sort, order=args.order, **filters,
        )

    print()
    print(f"=== 리뷰 목록 ({_describe_filters(filters)}, {page}/{total_pages} 페이지, 총 {total}건) ===")

    text_width = 200 if args.full else 40
    for row in rows:
        date = row["review_date"] or "----------"
        body = truncate(row["review_text"], text_width) if not args.full else row["review_text"]
        print(
            f"[{row['id']:>3}] {stars(row['rating'])} | {date} | "
            f"{pad(body, text_width)} | {format_sentiment(row)}"
        )

    print()
    if page < total_pages:
        print(f"다음 페이지: --page {page + 1}")
    print()
    return 0


# --------------------------------------------------------------------- show

def cmd_show(args, cfg: dict[str, Any]) -> int:
    db_path = config_module.resolve_path(cfg["paths"]["db"])
    with Database(db_path) as db:
        row = db.get_review(args.id)
        if row is None:
            logger.error("ID=%d 리뷰를 찾을 수 없습니다.", args.id)
            return 1
        raw = db.get_raw(row["raw_id"]) if row["raw_id"] else None

    line = "=" * 60
    print()
    print(line)
    print(f"리뷰 상세 (ID={row['id']})")
    print(line)
    print(f"제품     : {row['product'] or '-'}")
    print(f"카테고리 : {row['category'] or '-'}")
    print(f"별점     : {stars(row['rating'])} ({row['rating'] if row['rating'] else '없음'})")
    print(f"작성일   : {row['review_date'] or '없음'}")
    print(f"언어     : {row['lang'] or '-'}")
    print(f"글자 수  : {row['text_len']}자")
    print()
    print("[원문]")
    print(row["review_text"])
    print()
    print("[AI 감정 분석]")
    if row["sentiment"]:
        ko = SENTIMENT_KO.get(row["sentiment"], row["sentiment"])
        print(f"감정     : {row['sentiment']} ({ko})")
        print(f"신뢰도   : {row['score']:.2f}" if row["score"] is not None else "신뢰도   : -")
        print(f"키워드   : {row['keywords'] or '-'}")
        print(f"모델     : {row['sentiment_model'] or '-'}")
        print(f"분석일시 : {row['analyzed_at'] or '-'}")
    else:
        print("아직 분석되지 않았습니다. python main.py analyze --id", row["id"])

    if raw:
        print()
        print("[원본 정보]")
        print(f"출처 파일: {raw['source_file']} ({raw['source_row']}행)" if raw["source_row"]
              else f"출처: {raw['source_file']}")
        print(f"원본 별점: {raw['rating'] or '없음'} / 원본 날짜: {raw['review_date'] or '없음'}")
        try:
            raw_json = json.loads(raw["raw_json"] or "{}")
            if raw_json:
                print(f"원본 행  : {json.dumps(raw_json, ensure_ascii=False)[:200]}")
        except json.JSONDecodeError:
            pass

    print(line)
    print()
    return 0


# -------------------------------------------------------------------- stats

def build_stats(db: Database, **filters) -> dict[str, Any]:
    """통계 요약에 필요한 수치를 한 번에 모은다. 리포트에서도 재사용한다."""
    total = db.count_reviews(**filters)
    analyzed = db.count_reviews(analyzed=True, **filters)
    sentiment_counts = db.sentiment_counts(**filters)
    rating_counts = db.rating_counts(**filters)
    averages = db.averages(**filters)
    date_from, date_to = db.date_range(**filters)
    counts = db.counts_summary()

    return {
        "total": total,
        "analyzed": analyzed,
        "analyzed_rate": (analyzed / total * 100) if total else 0.0,
        "sentiment_counts": sentiment_counts,
        "rating_counts": rating_counts,
        "rated_total": sum(rating_counts.values()),
        "avg_rating": averages["avg_rating"],
        "avg_score": averages["avg_score"],
        "date_from": date_from,
        "date_to": date_to,
        "raw_total": counts["raw"],
        "clean_total": counts["clean"],
    }


def print_stats(stats: dict[str, Any], scope: str = "전체") -> None:
    print()
    print("=== 리뷰 분석 통계 ===")
    if scope != "전체":
        print(f"조건: {scope}")
    if stats["date_from"]:
        print(f"기간: {stats['date_from']} ~ {stats['date_to']}")
    print(f"총 리뷰 수: {stats['total']}건")
    print(f"분석 완료: {stats['analyzed']}건 ({stats['analyzed_rate']:.1f}%)")

    print()
    print("[감정 분포]")
    if stats["analyzed"]:
        for label in ("positive", "neutral", "negative"):
            count = stats["sentiment_counts"].get(label, 0)
            ratio = count / stats["analyzed"] * 100
            print(f"- {SENTIMENT_KO[label]}: {count}건 ({ratio:.1f}%)")
    else:
        print("- 분석된 리뷰가 없습니다. python main.py analyze --unanalyzed")

    print()
    print("[별점 분포]")
    if stats["rated_total"]:
        for rating in range(5, 0, -1):
            count = stats["rating_counts"].get(rating, 0)
            ratio = count / stats["rated_total"] * 100
            print(f"- {stars(rating)}: {count}건 ({ratio:.1f}%)")
    else:
        print("- 별점 정보가 있는 리뷰가 없습니다.")

    print()
    if stats["avg_rating"] is not None:
        print(f"평균 별점: {stats['avg_rating']:.2f}")
    if stats["avg_score"] is not None:
        print(f"평균 감정 점수: {stats['avg_score']:.2f}")
    print()


def cmd_stats(args, cfg: dict[str, Any]) -> int:
    filters = _collect_filters(args)
    db_path = config_module.resolve_path(cfg["paths"]["db"])
    with Database(db_path) as db:
        stats = build_stats(db, **filters)

    print_stats(stats, scope=_describe_filters(filters))
    return 0
