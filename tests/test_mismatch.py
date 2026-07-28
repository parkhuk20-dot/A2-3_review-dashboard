"""별점–감정 불일치 분석 검증.

방향(숨은 불만 / 관대한 본문) 구분과 어긋난 폭 계산이 핵심.
여기가 틀리면 "별점이 놓친 리뷰"를 반대로 집계하게 된다.
"""

from __future__ import annotations

import pytest

from src.mismatch import (
    GENEROUS_TEXT,
    HIDDEN_COMPLAINT,
    build_lines,
    expected_sentiment,
    find_mismatches,
    worst_rating_bucket,
)


class TestExpectedSentiment:
    @pytest.mark.parametrize("rating,expected", [
        (5, "positive"), (4, "positive"),
        (3, "neutral"),
        (2, "negative"), (1, "negative"),
    ])
    def test_별점에서_기대_감정을_뽑는다(self, rating, expected):
        assert expected_sentiment(rating) == expected


class TestFindMismatches:
    def test_일치하는_리뷰는_불일치로_잡히지_않는다(self, db, add_review):
        add_review("정말 만족합니다", rating=5, sentiment="positive")
        add_review("최악입니다 정말", rating=1, sentiment="negative")
        add_review("그저 그렇습니다", rating=3, sentiment="neutral")

        result = find_mismatches(db)
        assert result["comparable"] == 3
        assert result["mismatches"] == []

    def test_별점은_높은데_본문이_부정이면_숨은_불만(self, db, add_review):
        add_review("배송은 왔는데 품질이 아쉽습니다", rating=5, sentiment="negative")

        result = find_mismatches(db)
        assert len(result["mismatches"]) == 1
        item = result["mismatches"][0]
        assert item["label"] == HIDDEN_COMPLAINT
        assert item["gap"] == -2      # positive 기대(+1) → negative 실제(-1)
        assert result["hidden"] == 1

    def test_별점은_낮은데_본문이_긍정이면_관대한_본문(self, db, add_review):
        add_review("한 가지만 빼면 아주 좋습니다", rating=1, sentiment="positive")

        item = find_mismatches(db)["mismatches"][0]
        assert item["label"] == GENEROUS_TEXT
        assert item["gap"] == 2

    def test_중간_별점의_부정도_잡는다(self, db, add_review):
        # ★3인데 본문은 부정 — 별점만 보면 '중립'으로 묻히는 대표 사례
        add_review("먼지통이 작아서 자주 비워야 합니다", rating=3, sentiment="negative")

        item = find_mismatches(db)["mismatches"][0]
        assert item["gap"] == -1
        assert item["label"] == HIDDEN_COMPLAINT

    def test_min_gap으로_정반대인_경우만_거를_수_있다(self, db, add_review):
        add_review("살짝 아쉬운 부분이 있습니다", rating=3, sentiment="negative")    # gap -1
        add_review("겉보기와 달리 문제가 많습니다", rating=5, sentiment="negative")  # gap -2

        assert len(find_mismatches(db, min_gap=1)["mismatches"]) == 2
        assert len(find_mismatches(db, min_gap=2)["mismatches"]) == 1

    def test_kind로_방향을_고를_수_있다(self, db, add_review):
        add_review("별점은 높지만 불만이 있습니다", rating=5, sentiment="negative")
        add_review("별점은 낮지만 쓸만은 합니다", rating=1, sentiment="positive")

        hidden = find_mismatches(db, kind="hidden")["mismatches"]
        generous = find_mismatches(db, kind="generous")["mismatches"]

        assert len(hidden) == 1 and hidden[0]["label"] == HIDDEN_COMPLAINT
        assert len(generous) == 1 and generous[0]["label"] == GENEROUS_TEXT

    def test_어긋난_폭이_큰_순으로_정렬한다(self, db, add_review):
        add_review("조금 아쉬운 점이 있습니다", rating=3, sentiment="negative")     # gap -1
        add_review("기대와 전혀 다른 제품입니다", rating=5, sentiment="negative")   # gap -2

        gaps = [abs(m["gap"]) for m in find_mismatches(db)["mismatches"]]
        assert gaps == sorted(gaps, reverse=True)

    def test_별점이_없는_리뷰는_비교에서_빠진다(self, db, add_review):
        add_review("별점 없이 등록된 리뷰입니다", rating=None, sentiment="negative")
        assert find_mismatches(db)["comparable"] == 0

    def test_미분석_리뷰는_비교에서_빠진다(self, db, add_review):
        add_review("아직 분석되지 않은 리뷰입니다", rating=5, sentiment=None)
        assert find_mismatches(db)["comparable"] == 0

    def test_별점별_분포를_함께_계산한다(self, db, add_review):
        add_review("삼점인데 부정적인 리뷰입니다", rating=3, sentiment="negative")
        add_review("삼점이고 중립적인 리뷰입니다", rating=3, sentiment="neutral")

        total, mismatched = find_mismatches(db)["per_rating"][3]
        assert (total, mismatched) == (2, 1)

    def test_필터를_적용할_수_있다(self, db, add_review):
        add_review("이어폰 숨은 불만 리뷰", rating=5, sentiment="negative", product="이어폰")
        add_review("청소기 숨은 불만 리뷰", rating=5, sentiment="negative", product="청소기")

        assert len(find_mismatches(db, product="이어폰")["mismatches"]) == 1


class TestWorstRatingBucket:
    def test_불일치율이_가장_높은_별점을_찾는다(self):
        per_rating = {5: [20, 1], 3: [10, 5], 1: [10, 0]}
        rating, rate = worst_rating_bucket(per_rating, min_base=3)
        assert rating == 3
        assert rate == pytest.approx(50.0)

    def test_표본이_작은_구간은_제외한다(self):
        # 1건 중 1건 불일치(100%)는 통계로 쓸 수 없다.
        per_rating = {5: [1, 1], 3: [10, 2]}
        rating, _ = worst_rating_bucket(per_rating, min_base=3)
        assert rating == 3

    def test_불일치가_전혀_없으면_None(self):
        assert worst_rating_bucket({5: [10, 0]}, min_base=3) is None


class TestBuildLines:
    def test_비교할_리뷰가_없으면_안내한다(self, db):
        lines = build_lines(find_mismatches(db))
        assert any("비교할 수 없습니다" in line for line in lines)

    def test_불일치가_있으면_해석_문장을_붙인다(self, db, add_review):
        for i in range(4):
            add_review(f"삼점 부정 리뷰 번호 {i}", rating=3, sentiment="negative")
        for i in range(4):
            add_review(f"오점 긍정 리뷰 번호 {i}", rating=5, sentiment="positive")

        text = "\n".join(build_lines(find_mismatches(db)))
        assert "[해석]" in text
        assert "★3" in text
        assert HIDDEN_COMPLAINT in text
