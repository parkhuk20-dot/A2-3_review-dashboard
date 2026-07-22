"""정제 단계: raw_reviews → clean_reviews.

적용 규칙
    1. 필수 필드 검증   — 리뷰 본문이 없으면 버린다
    2. 텍스트 정규화     — HTML 태그·제어문자·중복 공백 정리
    3. 별점 범위 검증   — 1~5 를 벗어나면 NULL 로 두고 경고
    4. 날짜 형식 통일   — 여러 표기를 YYYY-MM-DD 로
    5. 짧은 리뷰 필터링 — min_length 미만은 버린다
    + 중복 처리(skip/upsert), 언어 판정(ko/en) [보너스]
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

from src import config as config_module
from src.db import Database

logger = logging.getLogger(__name__)

_HTML_TAG = re.compile(r"<[^>]+>")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_SPACE = re.compile(r"[ \t　]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
_LATIN = re.compile(r"[A-Za-z]")

# 실무 데이터에서 흔히 섞여 들어오는 날짜 표기들
_DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y년 %m월 %d일",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
    "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d",
]


def normalize_text(text: str, strip_html: bool = True) -> str:
    """리뷰 본문 정규화. 의미는 보존하고 형태만 정리한다."""
    if not text:
        return ""

    # 전각/반각 등 표기 차이를 하나로 모아 중복 판정이 흔들리지 않게 한다.
    result = unicodedata.normalize("NFKC", str(text))

    if strip_html:
        result = _HTML_TAG.sub(" ", result)
        # 태그를 지우면 남는 흔한 엔티티들도 되돌린다.
        for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                             ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
            result = result.replace(entity, char)

    result = _CONTROL_CHARS.sub("", result)
    result = _MULTI_SPACE.sub(" ", result)
    result = _MULTI_NEWLINE.sub("\n\n", result)
    return result.strip()


def parse_rating(value: Any, rating_min: int = 1, rating_max: int = 5) -> tuple[int | None, str | None]:
    """별점을 정수로 바꾸고 범위를 검증한다.

    반환: (별점 또는 None, 경고 메시지 또는 None)
    범위를 벗어나면 리뷰 자체는 살리고 별점만 NULL 로 둔다.
    """
    if value is None or str(value).strip() == "":
        return None, None

    text = str(value).strip()
    # "★★★★☆" 나 "4점" 같은 표기도 받아준다.
    stars = text.count("★") or text.count("*")
    if stars:
        number: float | None = float(stars)
    else:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        number = float(match.group()) if match else None

    if number is None:
        return None, f"별점을 숫자로 해석할 수 없습니다: {text!r}"

    rating = int(round(number))
    if rating < rating_min or rating > rating_max:
        return None, f"별점이 허용 범위({rating_min}~{rating_max})를 벗어납니다: {text!r}"

    return rating, None


def parse_date(value: Any) -> tuple[str | None, str | None]:
    """여러 날짜 표기를 YYYY-MM-DD 로 통일한다."""
    if value is None or str(value).strip() == "":
        return None, None

    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d"), None
        except ValueError:
            continue

    # 마지막 시도: ISO 8601(타임존 포함 등)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d"), None
    except ValueError:
        return None, f"날짜 형식을 해석할 수 없습니다: {text!r}"


def detect_language(text: str) -> str:
    """[보너스] 한글/라틴 문자 비율로 언어를 판정한다.

    외부 언어감지 라이브러리 없이도 한국어·영어 구분에는 충분하다.
    """
    hangul = len(_HANGUL.findall(text))
    latin = len(_LATIN.findall(text))

    if hangul == 0 and latin == 0:
        return "unknown"
    # 한글이 조금이라도 의미 있게 섞이면 한국어 리뷰로 본다.
    return "ko" if hangul >= max(2, latin * 0.2) else "en"


def make_hash(text: str, product: str | None) -> str:
    """중복 판정 키. 리뷰는 URL 같은 고유키가 없어 내용 기반으로 만든다.

    공백·문장부호를 지운 텍스트 + 제품명으로 해시해, 사소한 표기 차이는
    같은 리뷰로 보되 제품이 다르면 다른 리뷰로 본다.
    """
    stripped = re.sub(r"[\s\W_]+", "", text.lower())
    key = f"{stripped}|{(product or '').strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def clean_reviews(
    db: Database, cfg: dict[str, Any],
    dedup_policy: str | None = None,
    min_length: int | None = None,
    limit: int | None = None,
    reclean: bool = False,
) -> dict[str, int]:
    """미정제 원본을 정제해 clean_reviews 에 적재한다."""
    clean_cfg = cfg.get("clean", {})
    policy = dedup_policy or clean_cfg.get("dedup_policy", "skip")
    min_len = min_length if min_length is not None else clean_cfg.get("min_length", 10)
    rating_min = clean_cfg.get("rating_min", 1)
    rating_max = clean_cfg.get("rating_max", 5)
    strip_html = clean_cfg.get("strip_html", True)

    if reclean:
        rows = db.conn.execute(
            "SELECT * FROM raw_reviews ORDER BY id" + (" LIMIT ?" if limit else ""),
            (limit,) if limit else (),
        ).fetchall()
    else:
        rows = db.fetch_uncleaned_raw(limit)

    logger.info("정제 대상: %d건 (중복 정책=%s, 최소 길이=%d자)", len(rows), policy, min_len)

    stats = {
        "target": len(rows), "inserted": 0, "updated": 0, "duplicate": 0,
        "too_short": 0, "no_text": 0, "bad_rating": 0, "bad_date": 0,
    }
    processed_ids: list[int] = []

    for row in rows:
        raw_id = row["id"]
        processed_ids.append(raw_id)

        # 규칙 1·2: 필수 필드 검증 + 텍스트 정규화
        text = normalize_text(row["review_text"] or "", strip_html=strip_html)
        if not text:
            stats["no_text"] += 1
            logger.warning("raw#%d: 리뷰 본문이 비어 있어 제외합니다.", raw_id)
            continue

        # 규칙 5: 짧은 리뷰 필터링
        if len(text) < min_len:
            stats["too_short"] += 1
            logger.warning("raw#%d: 리뷰가 %d자로 너무 짧아 제외합니다 (%r)", raw_id, len(text), text)
            continue

        # 규칙 3: 별점 범위 검증
        rating, rating_warning = parse_rating(row["rating"], rating_min, rating_max)
        if rating_warning:
            stats["bad_rating"] += 1
            logger.warning("raw#%d: %s → 별점 없음으로 처리", raw_id, rating_warning)

        # 규칙 4: 날짜 형식 통일
        review_date, date_warning = parse_date(row["review_date"])
        if date_warning:
            stats["bad_date"] += 1
            logger.warning("raw#%d: %s → 날짜 없음으로 처리", raw_id, date_warning)

        product = (row["product"] or "").strip() or None
        record = {
            "raw_id": raw_id,
            "review_hash": make_hash(text, product),
            "review_text": text,
            "rating": rating,
            "review_date": review_date,
            "product": product,
            "category": (row["category"] or "").strip() or None,
            "lang": detect_language(text),
            "text_len": len(text),
        }

        result = db.upsert_clean(record, policy=policy)
        if result == "inserted":
            stats["inserted"] += 1
        elif result == "updated":
            stats["updated"] += 1
            stats["duplicate"] += 1
        else:
            stats["duplicate"] += 1
            logger.info("raw#%d: 중복 리뷰라 건너뜁니다 (정책=skip)", raw_id)

    db.mark_raw_cleaned(processed_ids)
    return stats


# --------------------------------------------------------------------- CLI

def cmd_clean(args, cfg: dict[str, Any]) -> int:
    db_path = config_module.resolve_path(cfg["paths"]["db"])
    with Database(db_path) as db:
        stats = clean_reviews(
            db, cfg,
            dedup_policy=args.dedup, min_length=args.min_length,
            limit=args.limit, reclean=args.reclean,
        )
        total_clean = db.counts_summary()["clean"]

    dropped = stats["no_text"] + stats["too_short"]
    logger.info(
        "정제 완료: 신규 %d건, 갱신 %d건, 중복 %d건, 제외 %d건 (본문없음 %d · 너무짧음 %d)",
        stats["inserted"], stats["updated"], stats["duplicate"], dropped,
        stats["no_text"], stats["too_short"],
    )
    if stats["bad_rating"] or stats["bad_date"]:
        logger.info(
            "값 보정: 별점 범위 이탈 %d건, 날짜 해석 실패 %d건 (해당 필드만 비움)",
            stats["bad_rating"], stats["bad_date"],
        )
    logger.info("clean 저장소 누적: %d건", total_clean)
    logger.info("다음 단계: python main.py analyze --unanalyzed")
    return 0
