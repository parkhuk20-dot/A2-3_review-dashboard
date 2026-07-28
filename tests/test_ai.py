"""AI 응답 정규화 계층 검증.

모델은 같은 뜻을 여러 형태로 답한다(한국어 라벨, 대문자, 0~100 척도, 문자열 리스트).
이 계층이 흡수하지 못하면 파싱이 깨지거나 잘못된 값이 저장된다.
"""

from __future__ import annotations

import pytest

from src.ai.extractor import _as_list, _complaint_types_to_text
from src.ai.sentiment import mock_sentiment, normalize_label, normalize_score


class TestNormalizeLabel:
    @pytest.mark.parametrize("raw,expected", [
        ("positive", "positive"),
        ("POSITIVE", "positive"),
        ("  Positive  ", "positive"),
        ("긍정", "positive"),
        ("pos", "positive"),
        ("negative", "negative"),
        ("부정", "negative"),
        ("neutral", "neutral"),
        ("중립", "neutral"),
        ("mixed", "neutral"),      # 혼합은 중립으로 흡수
    ])
    def test_다양한_표기를_표준_라벨로_모은다(self, raw, expected):
        assert normalize_label(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "행복", "unknown"])
    def test_알_수_없는_라벨은_None(self, raw):
        # None 을 돌려줘야 호출한 쪽이 '실패로 보고 스킵'할 수 있다.
        assert normalize_label(raw) is None


class TestNormalizeScore:
    @pytest.mark.parametrize("raw,expected", [
        (0.9, 0.9),
        (0.0, 0.0),
        (1.0, 1.0),
        ("0.75", 0.75),
    ])
    def test_정상_범위는_그대로(self, raw, expected):
        assert normalize_score(raw) == expected

    def test_0에서_100_척도로_답하면_보정한다(self):
        assert normalize_score(92) == 0.92

    def test_100을_넘으면_1로_자른다(self):
        assert normalize_score(150) == 1.0

    def test_음수는_0으로_자른다(self):
        assert normalize_score(-0.5) == 0.0

    @pytest.mark.parametrize("raw", [None, "abc", ""])
    def test_해석_불가하면_중간값_05(self, raw):
        assert normalize_score(raw) == 0.5


class TestMockSentiment:
    def test_높은_별점은_긍정(self):
        result = mock_sentiment("배송이 빠릅니다", rating=5)
        assert result["sentiment"] == "positive"

    def test_낮은_별점은_부정(self):
        assert mock_sentiment("불량입니다", rating=1)["sentiment"] == "negative"

    def test_중간_별점은_중립(self):
        assert mock_sentiment("무난합니다", rating=3)["sentiment"] == "neutral"

    def test_별점이_없으면_본문_감정어로_판단한다(self):
        assert mock_sentiment("정말 만족스럽고 훌륭합니다", None)["sentiment"] == "positive"
        assert mock_sentiment("불량에 배송 지연까지 최악", None)["sentiment"] == "negative"

    def test_점수는_항상_0과_1_사이(self):
        for rating in (None, 1, 2, 3, 4, 5):
            score = mock_sentiment("좋고 만족스럽고 훌륭하고 빠르고 편리함", rating)["score"]
            assert 0.0 <= score <= 1.0


class TestAsList:
    def test_리스트는_그대로_정리한다(self):
        assert _as_list(["배송", " 품질 ", ""]) == ["배송", "품질"]

    def test_문자열로_답해도_리스트로_바꾼다(self):
        assert _as_list("배송, 품질; 가격") == ["배송", "품질", "가격"]

    def test_개수를_제한한다(self):
        assert len(_as_list([str(i) for i in range(50)], limit=5)) == 5

    @pytest.mark.parametrize("raw", [None, {}, 123])
    def test_예상_밖_타입은_빈_리스트(self, raw):
        assert _as_list(raw) == []


class TestComplaintTypesToText:
    def test_딕셔너리_목록을_번호_매긴_텍스트로(self):
        text = _complaint_types_to_text([
            {"type": "배송 문제", "count": 12, "detail": "지연이 잦음"},
        ])
        assert "1. 배송 문제 (12건): 지연이 잦음" == text

    def test_count가_없어도_동작한다(self):
        assert _complaint_types_to_text([{"type": "품질"}]) == "1. 품질"

    def test_문자열로_답하면_그대로_쓴다(self):
        assert _complaint_types_to_text("배송 지연") == "배송 지연"

    def test_빈_값은_빈_문자열(self):
        assert _complaint_types_to_text(None) == ""
