"""테스트 공용 픽스처."""

from __future__ import annotations

import pytest

from src.cleaner import make_hash
from src.db import Database


@pytest.fixture
def db(tmp_path):
    """테스트마다 격리된 임시 SQLite DB."""
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def add_review(db):
    """clean_reviews + sentiments 에 리뷰 1건을 넣는 헬퍼를 돌려준다.

    조회·집계·알림 테스트가 공통으로 쓴다.
    """

    def _add(text, rating=None, date=None, sentiment=None, score=0.8,
             product=None, keywords=None, lang="ko"):
        db.upsert_clean({
            "raw_id": None,
            "review_hash": make_hash(text, product),
            "review_text": text,
            "rating": rating,
            "review_date": date,
            "product": product,
            "category": None,
            "lang": lang,
            "text_len": len(text),
        })
        row = db.conn.execute(
            "SELECT id FROM clean_reviews WHERE review_hash = ?",
            (make_hash(text, product),),
        ).fetchone()
        review_id = row["id"]

        if sentiment:
            db.save_sentiment(review_id, sentiment, score, keywords, "test-model")
        return review_id

    return _add
