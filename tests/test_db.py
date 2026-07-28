"""저장소 계층 검증: 중복 정책, 필터, 집계.

중복 정책이 어긋나면 데이터가 조용히 불어나거나 덮어써지고,
필터가 어긋나면 리포트 숫자가 전부 틀어진다.
"""

from __future__ import annotations

from src.cleaner import make_hash


def _record(text, product=None, rating=None, date=None, lang="ko"):
    return {
        "raw_id": None,
        "review_hash": make_hash(text, product),
        "review_text": text,
        "rating": rating,
        "review_date": date,
        "product": product,
        "category": None,
        "lang": lang,
        "text_len": len(text),
    }


# ------------------------------------------------------------ 중복 정책

class TestDedupPolicy:
    def test_처음_저장하면_inserted(self, db):
        assert db.upsert_clean(_record("배송이 빠릅니다"), policy="skip") == "inserted"

    def test_skip_정책은_중복을_건너뛴다(self, db):
        db.upsert_clean(_record("배송이 빠릅니다"), policy="skip")
        assert db.upsert_clean(_record("배송이 빠릅니다"), policy="skip") == "skipped"
        assert db.counts_summary()["clean"] == 1

    def test_upsert_정책은_기존_행을_갱신한다(self, db):
        db.upsert_clean(_record("배송이 빠릅니다", rating=3), policy="skip")
        result = db.upsert_clean(_record("배송이 빠릅니다", rating=5), policy="upsert")

        assert result == "updated"
        assert db.counts_summary()["clean"] == 1  # 행이 늘지 않아야 한다
        assert db.query_reviews()[0]["rating"] == 5  # 값은 새것으로

    def test_upsert는_updated_at을_채운다(self, db):
        db.upsert_clean(_record("배송이 빠릅니다"), policy="skip")
        db.upsert_clean(_record("배송이 빠릅니다"), policy="upsert")
        assert db.query_reviews()[0]["cleaned_at"] is not None
        assert db.conn.execute(
            "SELECT updated_at FROM clean_reviews"
        ).fetchone()["updated_at"] is not None

    def test_다른_리뷰는_각각_저장된다(self, db):
        db.upsert_clean(_record("배송이 빠릅니다"), policy="skip")
        db.upsert_clean(_record("포장이 꼼꼼합니다"), policy="skip")
        assert db.counts_summary()["clean"] == 2


# ---------------------------------------------------------------- 감정 저장

class TestSentiment:
    def test_감정을_저장하고_조회한다(self, add_review, db):
        review_id = add_review("배송이 빠릅니다", rating=5, sentiment="positive", score=0.9)
        row = db.get_review(review_id)
        assert row["sentiment"] == "positive"
        assert row["score"] == 0.9

    def test_재분석하면_덮어쓴다(self, add_review, db):
        review_id = add_review("배송이 빠릅니다", sentiment="neutral", score=0.5)
        db.save_sentiment(review_id, "positive", 0.95, "배송", "gpt-4o-mini")

        assert db.counts_summary()["analyzed"] == 1  # 행이 늘지 않아야 한다
        assert db.get_review(review_id)["sentiment"] == "positive"


# ------------------------------------------------------------------ 필터

class TestFilters:
    def test_감정으로_거른다(self, add_review, db):
        add_review("좋아요 정말로", rating=5, sentiment="positive")
        add_review("별로입니다 아주", rating=1, sentiment="negative")
        assert db.count_reviews(sentiment="negative") == 1

    def test_별점_최소_최대로_거른다(self, add_review, db):
        for rating in (1, 3, 5):
            add_review(f"별점 {rating}점 리뷰입니다", rating=rating, sentiment="neutral")

        assert db.count_reviews(rating_min=3) == 2
        assert db.count_reviews(rating_max=3) == 2
        assert db.count_reviews(rating=3) == 1

    def test_기간으로_거른다(self, add_review, db):
        add_review("6월 리뷰입니다", date="2026-06-15", sentiment="neutral")
        add_review("7월 리뷰입니다", date="2026-07-15", sentiment="neutral")

        assert db.count_reviews(date_from="2026-07-01") == 1
        assert db.count_reviews(date_to="2026-06-30") == 1

    def test_미분석만_거를_수_있다(self, add_review, db):
        add_review("분석된 리뷰입니다", sentiment="positive")
        add_review("분석_안된 리뷰입니다")

        assert db.count_reviews(analyzed=False) == 1
        assert db.count_reviews(analyzed=True) == 1

    def test_언어로_거른다(self, add_review, db):
        add_review("한국어 리뷰입니다", sentiment="neutral", lang="ko")
        add_review("English review here", sentiment="neutral", lang="en")
        assert db.count_reviews(lang="en") == 1

    def test_본문_키워드로_거른다(self, add_review, db):
        add_review("배송이 너무 느립니다", sentiment="negative")
        add_review("품질이 좋습니다 아주", sentiment="positive")
        assert db.count_reviews(keyword="배송") == 1

    def test_필터를_조합한다(self, add_review, db):
        add_review("배송 지연 불만입니다", rating=1, date="2026-07-10", sentiment="negative")
        add_review("배송 빠름 만족입니다", rating=5, date="2026-07-10", sentiment="positive")
        assert db.count_reviews(sentiment="negative", date_from="2026-07-01") == 1


# --------------------------------------------------------- 페이지네이션·정렬

class TestPagination:
    def test_limit과_offset으로_페이지를_나눈다(self, add_review, db):
        for i in range(5):
            add_review(f"리뷰 본문 번호 {i}", rating=3, sentiment="neutral")

        page1 = db.query_reviews(limit=2, offset=0, sort="id", order="asc")
        page2 = db.query_reviews(limit=2, offset=2, sort="id", order="asc")

        assert len(page1) == 2 and len(page2) == 2
        assert page1[0]["id"] != page2[0]["id"]

    def test_별점_내림차순_정렬(self, add_review, db):
        for rating in (1, 5, 3):
            add_review(f"별점 {rating}점짜리 리뷰", rating=rating, sentiment="neutral")

        ratings = [r["rating"] for r in db.query_reviews(sort="rating", order="desc")]
        assert ratings == [5, 3, 1]


# ------------------------------------------------------------------ 집계

class TestAggregation:
    def test_감정별_건수를_센다(self, add_review, db):
        add_review("좋아요 정말로", sentiment="positive")
        add_review("좋습니다 아주", sentiment="positive")
        add_review("별로예요 정말", sentiment="negative")

        assert db.sentiment_counts() == {"positive": 2, "negative": 1}

    def test_별점이_없는_리뷰는_별점_집계에서_빠진다(self, add_review, db):
        add_review("별점 있는 리뷰입니다", rating=5, sentiment="positive")
        add_review("별점 없는 리뷰입니다", rating=None, sentiment="positive")

        assert db.rating_counts() == {5: 1}

    def test_별점_감정_교차_집계(self, add_review, db):
        add_review("좋아요 정말로", rating=5, sentiment="positive")
        add_review("괜찮지만 아쉬움", rating=3, sentiment="negative")

        matrix = db.rating_sentiment_matrix()
        assert matrix[(5, "positive")] == 1
        assert matrix[(3, "negative")] == 1

    def test_일자별_감정_집계(self, add_review, db):
        add_review("첫날 리뷰입니다", date="2026-07-01", sentiment="positive")
        add_review("첫날 다른 리뷰", date="2026-07-01", sentiment="positive")
        add_review("둘째날 리뷰입니다", date="2026-07-02", sentiment="negative")

        daily = db.daily_sentiment_counts()
        assert daily["2026-07-01"]["positive"] == 2
        assert daily["2026-07-02"]["negative"] == 1

    def test_제품별_통계(self, add_review, db):
        add_review("이어폰 좋습니다", rating=5, product="이어폰", sentiment="positive")
        add_review("이어폰 별로예요", rating=1, product="이어폰", sentiment="negative")
        add_review("청소기 좋습니다", rating=4, product="청소기", sentiment="positive")

        stats = {row["name"]: row for row in db.product_stats()}
        assert stats["이어폰"]["n_reviews"] == 2
        assert stats["이어폰"]["avg_rating"] == 3.0
        assert stats["이어폰"]["negative"] == 1

    def test_날짜_범위를_구한다(self, add_review, db):
        add_review("이른 리뷰입니다", date="2026-06-01", sentiment="neutral")
        add_review("늦은 리뷰입니다", date="2026-07-15", sentiment="neutral")
        assert db.date_range() == ("2026-06-01", "2026-07-15")

    def test_빈_DB의_집계는_비어있다(self, db):
        assert db.sentiment_counts() == {}
        assert db.rating_counts() == {}
        assert db.date_range() == (None, None)
        assert db.counts_summary()["clean"] == 0
