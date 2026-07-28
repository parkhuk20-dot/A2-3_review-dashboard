"""정제 규칙 검증.

파일에서 들어오는 데이터는 형식이 제각각이라, 이 계층이 조용히 깨지면
잘못된 값이 그대로 DB 에 쌓인다. 경계값 위주로 확인한다.
"""

from __future__ import annotations

import pytest

from src.cleaner import (
    detect_language,
    make_hash,
    normalize_text,
    parse_date,
    parse_rating,
)


# ------------------------------------------------------------ 텍스트 정규화

class TestNormalizeText:
    def test_HTML_태그를_제거한다(self):
        assert normalize_text("<p>화면이 <b>선명</b>합니다</p>") == "화면이 선명 합니다"

    def test_HTML_엔티티를_되돌린다(self):
        assert normalize_text("가격&nbsp;대비&amp;성능") == "가격 대비&성능"

    def test_중복_공백을_한_칸으로_줄인다(self):
        assert normalize_text("   음질도   좋고    배송도    빨랐습니다.   ") == (
            "음질도 좋고 배송도 빨랐습니다."
        )

    def test_전각_문자를_반각으로_통일한다(self):
        # NFKC 정규화. 같은 리뷰가 표기만 달라 중복 판정이 어긋나는 것을 막는다.
        assert normalize_text("ＡＢＣ１２３") == "ABC123"

    def test_제어문자를_제거한다(self):
        assert normalize_text("정상\x00텍스트\x07입니다") == "정상텍스트입니다"

    def test_strip_html이_꺼지면_태그를_남긴다(self):
        assert normalize_text("<b>굵게</b>", strip_html=False) == "<b>굵게</b>"

    @pytest.mark.parametrize("value", ["", None])
    def test_빈_입력은_빈_문자열(self, value):
        assert normalize_text(value) == ""


# ----------------------------------------------------------------- 별점 검증

class TestParseRating:
    @pytest.mark.parametrize("raw,expected", [
        ("5", 5),
        ("1", 1),
        (3, 3),
        ("4점", 4),
        ("3.7", 4),      # 반올림
        ("3.2", 3),
        ("★★★★☆", 4),   # 별 기호 표기
    ])
    def test_유효한_별점을_정수로_바꾼다(self, raw, expected):
        rating, warning = parse_rating(raw)
        assert rating == expected
        assert warning is None

    @pytest.mark.parametrize("raw", ["0", "7", "-1", "100"])
    def test_범위를_벗어나면_None과_경고를_준다(self, raw):
        rating, warning = parse_rating(raw)
        # 리뷰 자체는 살리고 별점만 비우는 정책이라 None 이어야 한다.
        assert rating is None
        assert warning is not None and "범위" in warning

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_빈_값은_경고_없이_None(self, raw):
        assert parse_rating(raw) == (None, None)

    def test_숫자가_없으면_해석_실패_경고(self):
        rating, warning = parse_rating("없음")
        assert rating is None
        assert "숫자로 해석" in warning

    def test_허용_범위를_바꿀_수_있다(self):
        assert parse_rating("7", rating_min=1, rating_max=10)[0] == 7


# ----------------------------------------------------------------- 날짜 통일

class TestParseDate:
    @pytest.mark.parametrize("raw", [
        "2026-06-13",
        "2026/06/13",
        "2026.06.13",
        "20260613",
        "2026년 06월 13일",
        "2026-06-13 15:30:00",
        "2026-06-13 15:30",
    ])
    def test_다양한_표기를_YYYY_MM_DD로_통일한다(self, raw):
        date, warning = parse_date(raw)
        assert date == "2026-06-13"
        assert warning is None

    def test_ISO8601_타임존_표기도_받는다(self):
        assert parse_date("2026-06-13T15:30:00+09:00")[0] == "2026-06-13"

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_빈_값은_경고_없이_None(self, raw):
        assert parse_date(raw) == (None, None)

    def test_해석_불가한_값은_경고를_준다(self):
        date, warning = parse_date("작년 여름")
        assert date is None
        assert "날짜 형식" in warning


# --------------------------------------------------------------- 언어 판정

class TestDetectLanguage:
    def test_한국어_리뷰(self):
        assert detect_language("배송이 빠르고 포장도 꼼꼼합니다") == "ko"

    def test_영어_리뷰(self):
        assert detect_language("Battery life is amazing, lasts three days") == "en"

    def test_한글이_섞이면_한국어로_본다(self):
        # 브랜드명·모델명이 영문으로 섞이는 게 흔해서, 한글이 있으면 ko 로 본다.
        assert detect_language("FitPro 배터리가 사흘은 갑니다") == "ko"

    @pytest.mark.parametrize("text", ["12345", "!!!", ""])
    def test_문자가_없으면_unknown(self, text):
        assert detect_language(text) == "unknown"


# ------------------------------------------------------------ 중복 판정 해시

class TestMakeHash:
    def test_같은_리뷰는_같은_해시(self):
        assert make_hash("배송 빠릅니다", "이어폰") == make_hash("배송 빠릅니다", "이어폰")

    def test_공백과_문장부호_차이는_같은_리뷰로_본다(self):
        # "배송 빠릅니다!" 와 "배송  빠릅니다" 는 사실상 같은 리뷰다.
        assert make_hash("배송 빠릅니다!", "이어폰") == make_hash("배송  빠릅니다", "이어폰")

    def test_대소문자_차이도_같은_리뷰로_본다(self):
        assert make_hash("Fast Shipping", "A") == make_hash("fast shipping", "A")

    def test_제품이_다르면_다른_리뷰로_본다(self):
        # 같은 문구라도 다른 제품 리뷰면 별개로 남겨야 한다.
        assert make_hash("좋습니다 정말로", "이어폰") != make_hash("좋습니다 정말로", "청소기")

    def test_제품이_없어도_동작한다(self):
        assert make_hash("좋습니다", None) == make_hash("좋습니다", None)

    def test_내용이_다르면_다른_해시(self):
        assert make_hash("배송 빠름", "A") != make_hash("배송 느림", "A")
