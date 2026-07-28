"""감정 급증 알림 검증.

'건수'가 아니라 '비율'로 판정하는지, 데이터가 부족할 때 섣불리 경고하지 않는지가 핵심.
"""

from __future__ import annotations

import pytest

from src.alert import detect_spike

CFG = {"alert": {"days": 7, "threshold": 1.5, "min_reviews": 3}}


def _fill(add_review, dates_sentiments):
    """(날짜, 감정) 목록을 리뷰로 채운다."""
    for index, (date, sentiment) in enumerate(dates_sentiments):
        add_review(f"테스트 리뷰 본문 번호 {index}", rating=3, date=date, sentiment=sentiment)


class TestDetectSpike:
    def test_부정_비율이_임계_배수를_넘으면_경고한다(self, db, add_review):
        # 직전 주는 부정 1/4(25%), 최근 주는 부정 3/4(75%) → 3배
        _fill(add_review, [
            ("2026-07-02", "negative"), ("2026-07-03", "positive"),
            ("2026-07-04", "positive"), ("2026-07-05", "positive"),
            ("2026-07-10", "negative"), ("2026-07-11", "negative"),
            ("2026-07-12", "negative"), ("2026-07-15", "positive"),
        ])
        result = detect_spike(db, CFG)

        assert result["triggered"] is True
        assert result["recent_ratio"] == pytest.approx(0.75)
        assert result["prev_ratio"] == pytest.approx(0.25)
        assert result["ratio_change"] == pytest.approx(3.0)

    def test_비율이_비슷하면_경고하지_않는다(self, db, add_review):
        _fill(add_review, [
            ("2026-07-02", "negative"), ("2026-07-03", "positive"),
            ("2026-07-04", "positive"), ("2026-07-05", "positive"),
            ("2026-07-10", "negative"), ("2026-07-11", "positive"),
            ("2026-07-12", "positive"), ("2026-07-15", "positive"),
        ])
        assert detect_spike(db, CFG)["triggered"] is False

    def test_리뷰_총량이_늘어도_비율이_같으면_경고하지_않는다(self, db, add_review):
        # 건수로 판정하면 부정 1건 → 3건이라 경고가 뜨지만, 비율은 33%로 동일하다.
        _fill(add_review, [
            ("2026-07-02", "negative"), ("2026-07-03", "positive"), ("2026-07-04", "positive"),
            ("2026-07-10", "negative"), ("2026-07-10", "negative"), ("2026-07-11", "negative"),
            ("2026-07-11", "positive"), ("2026-07-12", "positive"), ("2026-07-12", "positive"),
            ("2026-07-15", "positive"), ("2026-07-15", "positive"), ("2026-07-15", "positive"),
        ])
        result = detect_spike(db, CFG)

        assert result["recent_ratio"] == pytest.approx(result["prev_ratio"], abs=0.02)
        assert result["triggered"] is False

    def test_데이터가_부족하면_경고하지_않고_이유를_남긴다(self, db, add_review):
        _fill(add_review, [("2026-07-15", "negative"), ("2026-07-14", "negative")])
        result = detect_spike(db, CFG)

        assert result["triggered"] is False
        assert "최소 리뷰 수" in result["reason"]

    def test_직전_기간에_부정이_없으면_최근_비율로_판정한다(self, db, add_review):
        # 배수 계산이 불가능한 구간. 최근 부정 비율 30% 이상이면 경고.
        _fill(add_review, [
            ("2026-07-02", "positive"), ("2026-07-03", "positive"), ("2026-07-04", "positive"),
            ("2026-07-10", "negative"), ("2026-07-11", "negative"), ("2026-07-15", "positive"),
        ])
        result = detect_spike(db, CFG)

        assert result["prev_ratio"] == 0
        assert result["triggered"] is True
        assert "직전 기간에 부정 리뷰가 없어" in result["reason"]

    def test_기준일은_오늘이_아니라_데이터의_마지막_날(self, db, add_review):
        # 과거 데이터셋으로 돌려도 의미 있는 비교가 되어야 한다.
        _fill(add_review, [
            ("2020-01-02", "negative"), ("2020-01-03", "positive"),
            ("2020-01-04", "positive"), ("2020-01-05", "positive"),
            ("2020-01-10", "negative"), ("2020-01-11", "negative"),
            ("2020-01-12", "negative"), ("2020-01-13", "positive"),
        ])
        result = detect_spike(db, CFG)

        assert result["anchor"] == "2020-01-13"
        assert result["triggered"] is True

    def test_날짜가_없으면_판정_불가로_None(self, db, add_review):
        add_review("날짜가 없는 리뷰입니다", sentiment="negative", date=None)
        assert detect_spike(db, CFG) is None

    def test_빈_DB에서도_예외가_나지_않는다(self, db):
        assert detect_spike(db, CFG) is None

    def test_임계값을_직접_지정할_수_있다(self, db, add_review):
        _fill(add_review, [
            ("2026-07-02", "negative"), ("2026-07-03", "positive"),
            ("2026-07-04", "positive"), ("2026-07-05", "positive"),
            ("2026-07-10", "negative"), ("2026-07-11", "negative"),
            ("2026-07-12", "negative"), ("2026-07-15", "positive"),
        ])
        # 3배 상승이므로 임계 5배로 올리면 경고가 뜨지 않아야 한다.
        assert detect_spike(db, CFG, threshold=5.0)["triggered"] is False
        assert detect_spike(db, CFG, threshold=2.0)["triggered"] is True
