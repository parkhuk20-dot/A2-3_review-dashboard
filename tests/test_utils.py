"""수집 매핑 · 집계 보조 · 콘솔 표시 유틸 검증."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.importer import _stringify, build_column_map
from src.viewer import display_width, pad, stars, truncate
from src.visualize import aggregate_by_week

ALIASES = {
    "review_text": ["review_text", "리뷰내용", "내용", "content"],
    "rating": ["rating", "별점", "평점"],
    "review_date": ["review_date", "date", "작성일"],
    "product": ["product", "제품명"],
}


# --------------------------------------------------------- 컬럼 별칭 매핑

class TestBuildColumnMap:
    def test_한국어_헤더를_매핑한다(self):
        mapping = build_column_map(["리뷰내용", "별점", "작성일"], ALIASES)
        assert mapping["review_text"] == "리뷰내용"
        assert mapping["rating"] == "별점"

    def test_영어_헤더를_매핑한다(self):
        mapping = build_column_map(["review_text", "rating", "date"], ALIASES)
        assert mapping["review_date"] == "date"

    def test_대소문자와_공백_밑줄_차이를_흡수한다(self):
        mapping = build_column_map(["Review Text", "  RATING  "], ALIASES)
        assert mapping["review_text"] == "Review Text"
        assert mapping["rating"] == "  RATING  "

    def test_매칭되지_않는_필드는_빠진다(self):
        mapping = build_column_map(["리뷰내용"], ALIASES)
        assert "review_text" in mapping
        assert "product" not in mapping

    def test_별칭_순서가_빠른_쪽을_고른다(self):
        # review_text 별칭 목록에서 'review_text' 가 '내용'보다 앞에 있다.
        mapping = build_column_map(["내용", "review_text"], ALIASES)
        assert mapping["review_text"] == "review_text"


class TestStringify:
    def test_엑셀_날짜를_문자열로_바꾼다(self):
        assert _stringify(datetime(2026, 6, 13)) == "2026-06-13"

    def test_엑셀_정수형_실수를_정수_문자열로(self):
        # openpyxl 이 별점 5 를 5.0 으로 주는 경우가 흔하다.
        assert _stringify(5.0) == "5"

    def test_앞뒤_공백을_제거한다(self):
        assert _stringify("  좋아요  ") == "좋아요"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_빈_값은_None(self, value):
        assert _stringify(value) is None


# ------------------------------------------------------------ 주 단위 집계

class TestAggregateByWeek:
    def test_같은_주는_하나로_묶인다(self):
        daily = {
            "2026-07-06": {"negative": 2},
            "2026-07-07": {"negative": 3},
            "2026-07-08": {"positive": 1},
        }
        weekly = aggregate_by_week(daily)

        assert len(weekly) == 1
        bucket = next(iter(weekly.values()))
        assert bucket["negative"] == 5
        assert bucket["positive"] == 1

    def test_집계_키는_항상_월요일(self):
        weekly = aggregate_by_week({"2026-07-09": {"positive": 1}})
        for key in weekly:
            assert datetime.strptime(key, "%Y-%m-%d").weekday() == 0

    def test_다른_주는_따로_묶인다(self):
        weekly = aggregate_by_week({
            "2026-07-06": {"positive": 1},   # 한 주
            "2026-07-13": {"positive": 1},   # 다음 주
        })
        assert len(weekly) == 2

    def test_총합은_보존된다(self):
        daily = {f"2026-07-{d:02d}": {"positive": 1} for d in range(1, 21)}
        weekly = aggregate_by_week(daily)
        assert sum(b["positive"] for b in weekly.values()) == 20

    def test_잘못된_날짜는_건너뛴다(self):
        weekly = aggregate_by_week({"날짜아님": {"positive": 1}, "2026-07-06": {"positive": 1}})
        assert len(weekly) == 1


# --------------------------------------------------------- 콘솔 표시 유틸

class TestDisplayWidth:
    def test_한글은_두_칸으로_센다(self):
        assert display_width("배송") == 4

    def test_영문은_한_칸으로_센다(self):
        assert display_width("abcd") == 4

    def test_혼용_문자열(self):
        assert display_width("배송abc") == 7


class TestTruncate:
    def test_짧으면_그대로_둔다(self):
        assert truncate("짧음", 20) == "짧음"

    def test_길면_말줄임표를_붙인다(self):
        result = truncate("배송이 너무 늦어서 정말 답답했던 리뷰입니다", 20)
        assert result.endswith("...")
        assert display_width(result) <= 20

    def test_줄바꿈을_공백으로_바꾼다(self):
        assert "\n" not in truncate("첫줄\n둘째줄", 50)


class TestPad:
    def test_한글_기준으로_폭을_맞춘다(self):
        assert display_width(pad("배송", 10)) == 10

    def test_이미_넘치면_자르지_않는다(self):
        assert pad("배송지연문제", 4) == "배송지연문제"


class TestStars:
    @pytest.mark.parametrize("rating,expected", [
        (5, "★★★★★"),
        (3, "★★★☆☆"),
        (1, "★☆☆☆☆"),
    ])
    def test_별점을_기호로_표시한다(self, rating, expected):
        assert stars(rating) == expected

    def test_별점이_없으면_폭을_맞춘_빈_표시(self):
        # 표 정렬이 어긋나지 않도록 5칸을 유지해야 한다.
        assert len(stars(None)) == 5
