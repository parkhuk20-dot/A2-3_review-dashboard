"""속성별 감정 분석(ABSA) 검증.

모델이 목록에 없는 속성을 지어내거나 같은 속성을 두 번 답하는 일이 실제로 있어서,
파싱 계층이 그걸 걸러내는지가 핵심이다.
"""

from __future__ import annotations

import pytest

from src.ai.aspects import (
    DEFAULT_ASPECTS,
    build_lines,
    mock_aspects,
    parse_aspects,
    resolve_aspects,
)

ASPECTS = ["배송", "품질", "가격", "고객서비스", "사용성", "디자인"]


class TestResolveAspects:
    def test_설정이_없으면_기본_목록(self):
        assert resolve_aspects({}) == DEFAULT_ASPECTS

    def test_설정이_있으면_그것을_쓴다(self):
        cfg = {"absa": {"aspects": ["맛", "양"]}}
        assert resolve_aspects(cfg) == ["맛", "양"]

    def test_기본_목록을_변형해도_원본은_안전하다(self):
        resolve_aspects({}).append("오염")
        assert "오염" not in DEFAULT_ASPECTS


class TestParseAspects:
    def test_정상_응답을_파싱한다(self):
        data = {"aspects": [
            {"aspect": "배송", "sentiment": "negative", "score": 0.9, "evidence": "일주일 걸림"},
            {"aspect": "품질", "sentiment": "positive", "score": 0.8, "evidence": "튼튼합니다"},
        ]}
        result = parse_aspects(data, ASPECTS)

        assert len(result) == 2
        assert result[0] == {
            "aspect": "배송", "sentiment": "negative", "score": 0.9, "evidence": "일주일 걸림",
        }

    def test_한_리뷰에서_속성마다_감정이_달라도_된다(self):
        # ABSA 의 존재 이유. 전체 감정 하나로는 이걸 표현할 수 없다.
        data = {"aspects": [
            {"aspect": "배송", "sentiment": "negative"},
            {"aspect": "품질", "sentiment": "positive"},
        ]}
        sentiments = {i["aspect"]: i["sentiment"] for i in parse_aspects(data, ASPECTS)}
        assert sentiments == {"배송": "negative", "품질": "positive"}

    def test_목록에_없는_속성은_버린다(self):
        # 모델이 임의로 속성을 만들어내는 경우를 막는다.
        data = {"aspects": [
            {"aspect": "배송", "sentiment": "positive"},
            {"aspect": "우주적_감동", "sentiment": "positive"},
        ]}
        result = parse_aspects(data, ASPECTS)
        assert [i["aspect"] for i in result] == ["배송"]

    def test_영문_속성명을_한국어로_맞춘다(self):
        data = {"aspects": [{"aspect": "delivery", "sentiment": "negative"}]}
        assert parse_aspects(data, ASPECTS)[0]["aspect"] == "배송"

    def test_같은_속성이_중복되면_첫_번째만_남긴다(self):
        # aspects 테이블이 (review_id, aspect) UNIQUE 라 중복이 들어오면 안 된다.
        data = {"aspects": [
            {"aspect": "배송", "sentiment": "negative"},
            {"aspect": "배송", "sentiment": "positive"},
        ]}
        result = parse_aspects(data, ASPECTS)
        assert len(result) == 1
        assert result[0]["sentiment"] == "negative"

    def test_감정_라벨이_이상하면_그_항목만_버린다(self):
        data = {"aspects": [
            {"aspect": "배송", "sentiment": "무엇"},
            {"aspect": "품질", "sentiment": "긍정"},
        ]}
        result = parse_aspects(data, ASPECTS)
        assert [i["aspect"] for i in result] == ["품질"]
        assert result[0]["sentiment"] == "positive"

    def test_점수가_없으면_중간값으로_채운다(self):
        data = {"aspects": [{"aspect": "배송", "sentiment": "positive"}]}
        assert parse_aspects(data, ASPECTS)[0]["score"] == 0.5

    def test_근거가_너무_길면_자른다(self):
        data = {"aspects": [
            {"aspect": "배송", "sentiment": "positive", "evidence": "가" * 500},
        ]}
        assert len(parse_aspects(data, ASPECTS)[0]["evidence"]) == 200

    @pytest.mark.parametrize("data", [{}, {"aspects": None}, {"aspects": "배송"}, {"aspects": []}])
    def test_형식이_어긋나면_빈_리스트(self, data):
        assert parse_aspects(data, ASPECTS) == []

    def test_리스트_안에_딕셔너리가_아닌_값이_있어도_넘어간다(self):
        data = {"aspects": ["배송", {"aspect": "품질", "sentiment": "positive"}]}
        assert len(parse_aspects(data, ASPECTS)) == 1


class TestMockAspects:
    def test_언급된_속성만_잡는다(self):
        result = mock_aspects("배송이 정말 빨랐습니다", rating=5, aspect_list=ASPECTS)
        assert [i["aspect"] for i in result] == ["배송"]

    def test_언급이_없으면_빈_리스트(self):
        # "좋아요" 같은 리뷰는 속성이 없는 게 정상이다.
        assert mock_aspects("좋아요 정말", rating=5, aspect_list=ASPECTS) == []

    def test_여러_속성을_동시에_잡는다(self):
        result = mock_aspects("배송도 빠르고 품질도 좋습니다", rating=5, aspect_list=ASPECTS)
        assert {i["aspect"] for i in result} == {"배송", "품질"}

    def test_부정어가_있으면_부정으로_본다(self):
        result = mock_aspects("배송이 너무 늦어서 최악입니다", rating=1, aspect_list=ASPECTS)
        assert result[0]["sentiment"] == "negative"

    def test_영어_리뷰도_잡는다(self):
        result = mock_aspects("Shipping took a week", rating=1, aspect_list=ASPECTS)
        assert "배송" in [i["aspect"] for i in result]


class TestAspectStats:
    def test_속성별_감정을_집계한다(self, db, add_review):
        review_id = add_review("배송은 느리지만 품질은 좋습니다", rating=3, sentiment="neutral")
        db.save_aspects(review_id, [
            {"aspect": "배송", "sentiment": "negative", "score": 0.9, "evidence": "느림"},
            {"aspect": "품질", "sentiment": "positive", "score": 0.8, "evidence": "좋음"},
        ], "test-model")

        stats = {s["aspect"]: s for s in db.aspect_stats()}
        assert stats["배송"]["negative"] == 1
        assert stats["품질"]["positive"] == 1

    def test_순감정은_긍정과_부정의_차이를_언급수로_나눈_값(self, db, add_review):
        for index, sentiment in enumerate(["negative", "negative", "positive", "neutral"]):
            review_id = add_review(f"배송 관련 리뷰 번호 {index}", rating=3, sentiment="neutral")
            db.save_aspects(review_id, [{"aspect": "배송", "sentiment": sentiment}], "test")

        stat = db.aspect_stats()[0]
        assert stat["mentions"] == 4
        assert stat["net"] == pytest.approx((1 - 2) / 4)   # -0.25
        assert stat["negative_rate"] == pytest.approx(50.0)

    def test_재분석하면_이전_속성이_남지_않는다(self, db, add_review):
        review_id = add_review("배송과 품질 모두 언급한 리뷰", rating=3, sentiment="neutral")
        db.save_aspects(review_id, [
            {"aspect": "배송", "sentiment": "negative"},
            {"aspect": "품질", "sentiment": "negative"},
        ], "test")
        # 재분석에서 '품질'이 빠지면 유령으로 남으면 안 된다.
        db.save_aspects(review_id, [{"aspect": "배송", "sentiment": "positive"}], "test")

        stats = db.aspect_stats()
        assert [s["aspect"] for s in stats] == ["배송"]
        assert stats[0]["positive"] == 1

    def test_필터가_적용된다(self, db, add_review):
        a = add_review("이어폰 배송 리뷰입니다", rating=5, sentiment="positive", product="이어폰")
        b = add_review("청소기 배송 리뷰입니다", rating=1, sentiment="negative", product="청소기")
        db.save_aspects(a, [{"aspect": "배송", "sentiment": "positive"}], "test")
        db.save_aspects(b, [{"aspect": "배송", "sentiment": "negative"}], "test")

        stats = db.aspect_stats(product="이어폰")
        assert stats[0]["mentions"] == 1
        assert stats[0]["positive"] == 1

    def test_분석_대상_선정은_속성이_없는_리뷰만(self, db, add_review):
        done = add_review("이미 속성 분석된 리뷰", rating=5, sentiment="positive")
        add_review("아직 속성 분석 안 된 리뷰", rating=5, sentiment="positive")
        db.save_aspects(done, [{"aspect": "배송", "sentiment": "positive"}], "test")

        targets = db.fetch_for_aspect_analysis(mode="unanalyzed")
        assert len(targets) == 1
        assert targets[0]["id"] != done

    def test_커버리지를_센다(self, db, add_review):
        review_id = add_review("배송과 품질을 언급한 리뷰", rating=3, sentiment="neutral")
        db.save_aspects(review_id, [
            {"aspect": "배송", "sentiment": "positive"},
            {"aspect": "품질", "sentiment": "negative"},
        ], "test")

        assert db.aspect_coverage() == {"reviews": 1, "rows": 2}


class TestBuildLines:
    def test_결과가_없으면_안내한다(self):
        assert any("실행하세요" in line for line in build_lines([]))

    def test_개선_우선순위와_해석을_붙인다(self):
        stats = [
            {"aspect": "배송", "mentions": 20, "positive": 2, "neutral": 3,
             "negative": 15, "net": -0.65, "negative_rate": 75.0},
            {"aspect": "품질", "mentions": 18, "positive": 14, "neutral": 2,
             "negative": 2, "net": 0.67, "negative_rate": 11.1},
        ]
        text = "\n".join(build_lines(stats, min_mentions=3))

        assert "[개선 우선순위]" in text
        assert "1. 배송" in text          # 부정이 가장 많은 속성이 1순위
        assert "[해석]" in text
        assert "품질" in text and "배송" in text

    def test_우선순위는_건수와_부정률을_함께_본다(self):
        # 건수만 보면 '품질'(13건)이 1위지만, 부정률은 39%로 오히려 양호하다.
        # '고객서비스'는 8건 중 7건(87.5%)이 부정이라 실제로는 더 급한 문제다.
        stats = [
            {"aspect": "품질", "mentions": 33, "positive": 16, "neutral": 4,
             "negative": 13, "net": 0.09, "negative_rate": 39.4},
            {"aspect": "고객서비스", "mentions": 8, "positive": 0, "neutral": 1,
             "negative": 7, "net": -0.88, "negative_rate": 87.5},
        ]
        text = "\n".join(build_lines(stats, min_mentions=3))
        assert "1. 고객서비스" in text
        assert "2. 품질" in text

    def test_표본이_너무_작은_속성은_우선순위에서_뺀다(self):
        # 2건 중 2건 부정(100%)은 비율만 보면 1위지만 판단 근거가 약하다.
        stats = [
            {"aspect": "디자인", "mentions": 2, "positive": 0, "neutral": 0,
             "negative": 2, "net": -1.0, "negative_rate": 100.0},
            {"aspect": "배송", "mentions": 16, "positive": 5, "neutral": 0,
             "negative": 11, "net": -0.38, "negative_rate": 68.8},
        ]
        text = "\n".join(build_lines(stats, min_mentions=3))
        assert "1. 배송" in text
        assert "디자인 —" not in text

    def test_부정이_없으면_우선순위를_만들지_않는다(self):
        stats = [{"aspect": "품질", "mentions": 10, "positive": 10, "neutral": 0,
                  "negative": 0, "net": 1.0, "negative_rate": 0.0}]
        assert "[개선 우선순위]" not in "\n".join(build_lines(stats))
