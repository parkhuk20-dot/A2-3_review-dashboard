"""SQLite 영구 저장소. 스키마 생성, 저장, 조회를 담당한다.

테이블 구성
    raw_reviews   : 파일에서 읽은 원본 (손대지 않고 보존)
    clean_reviews : 정제·검증을 통과한 데이터
    sentiments    : 리뷰별 AI 감정 분석 결과
    extractions   : 조건별 AI 키워드·요약 추출 결과
    import_log    : 파일 단위 수집 이력
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT,
    source_row  INTEGER,
    review_text TEXT,
    rating      TEXT,
    review_date TEXT,
    product     TEXT,
    category    TEXT,
    raw_json    TEXT,
    imported_at TEXT NOT NULL,
    is_cleaned  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clean_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id      INTEGER REFERENCES raw_reviews(id),
    review_hash TEXT UNIQUE,
    review_text TEXT NOT NULL,
    rating      INTEGER,
    review_date TEXT,
    product     TEXT,
    category    TEXT,
    lang        TEXT,
    text_len    INTEGER,
    cleaned_at  TEXT NOT NULL,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS sentiments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id   INTEGER UNIQUE REFERENCES clean_reviews(id) ON DELETE CASCADE,
    sentiment   TEXT NOT NULL,
    score       REAL,
    keywords    TEXT,
    model       TEXT,
    analyzed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_sentiment TEXT,
    scope_product   TEXT,
    date_from       TEXT,
    date_to         TEXT,
    n_reviews       INTEGER,
    pos_keywords    TEXT,
    neg_keywords    TEXT,
    summary         TEXT,
    complaint_types TEXT,
    suggestions     TEXT,
    model           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT,
    total       INTEGER,
    inserted    INTEGER,
    skipped     INTEGER,
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clean_date    ON clean_reviews(review_date);
CREATE INDEX IF NOT EXISTS idx_clean_product ON clean_reviews(product);
CREATE INDEX IF NOT EXISTS idx_raw_cleaned   ON raw_reviews(is_cleaned);
CREATE INDEX IF NOT EXISTS idx_sent_label    ON sentiments(sentiment);
"""

# list/show/dashboard 가 모두 같은 조인 기준을 쓰도록 한 곳에 모아둔다.
_BASE_SELECT = """
SELECT c.id, c.review_text, c.rating, c.review_date, c.product, c.category,
       c.lang, c.text_len, c.raw_id, c.cleaned_at,
       s.sentiment, s.score, s.keywords, s.model AS sentiment_model, s.analyzed_at
FROM clean_reviews c
LEFT JOIN sentiments s ON s.review_id = c.id
"""


def now() -> str:
    """ISO8601(초 단위) 현재 시각."""
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """SQLite 연결 래퍼. `with Database(path) as db:` 형태로 쓴다."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    # ------------------------------------------------------------------ 기본

    def _create_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -------------------------------------------------------------- raw 저장

    def insert_raw(self, record: dict[str, Any]) -> int:
        """원본 리뷰 1건 저장. 원본 행 전체는 raw_json 에 보존한다."""
        cur = self.conn.execute(
            """
            INSERT INTO raw_reviews
                (source_file, source_row, review_text, rating, review_date,
                 product, category, raw_json, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("source_file"),
                record.get("source_row"),
                record.get("review_text"),
                record.get("rating"),
                record.get("review_date"),
                record.get("product"),
                record.get("category"),
                json.dumps(record.get("raw", {}), ensure_ascii=False),
                now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def fetch_uncleaned_raw(self, limit: int | None = None) -> list[sqlite3.Row]:
        """아직 정제하지 않은 원본을 가져온다."""
        sql = "SELECT * FROM raw_reviews WHERE is_cleaned = 0 ORDER BY id"
        params: list[Any] = []
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def mark_raw_cleaned(self, raw_ids: Iterable[int]) -> None:
        ids = [(int(i),) for i in raw_ids]
        if not ids:
            return
        self.conn.executemany("UPDATE raw_reviews SET is_cleaned = 1 WHERE id = ?", ids)
        self.conn.commit()

    def log_import(self, source_file: str, total: int, inserted: int, skipped: int) -> None:
        self.conn.execute(
            "INSERT INTO import_log (source_file, total, inserted, skipped, imported_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (source_file, total, inserted, skipped, now()),
        )
        self.conn.commit()

    # ------------------------------------------------------------ clean 저장

    def upsert_clean(self, record: dict[str, Any], policy: str = "skip") -> str:
        """정제 결과를 저장한다.

        반환값: 'inserted' | 'updated' | 'skipped'
        중복 판정은 review_hash(정규화 텍스트 + 제품) 로 한다.
        SQLite 버전에 따라 lastrowid 동작이 달라, 사전 존재 확인으로 판정한다.
        """
        existing = self.conn.execute(
            "SELECT id FROM clean_reviews WHERE review_hash = ?", (record["review_hash"],)
        ).fetchone()

        if existing and policy == "skip":
            return "skipped"

        if existing:  # policy == "upsert"
            self.conn.execute(
                """
                UPDATE clean_reviews
                   SET raw_id = ?, review_text = ?, rating = ?, review_date = ?,
                       product = ?, category = ?, lang = ?, text_len = ?, updated_at = ?
                 WHERE review_hash = ?
                """,
                (
                    record.get("raw_id"), record["review_text"], record.get("rating"),
                    record.get("review_date"), record.get("product"), record.get("category"),
                    record.get("lang"), record.get("text_len"), now(), record["review_hash"],
                ),
            )
            self.conn.commit()
            return "updated"

        self.conn.execute(
            """
            INSERT INTO clean_reviews
                (raw_id, review_hash, review_text, rating, review_date,
                 product, category, lang, text_len, cleaned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("raw_id"), record["review_hash"], record["review_text"],
                record.get("rating"), record.get("review_date"), record.get("product"),
                record.get("category"), record.get("lang"), record.get("text_len"), now(),
            ),
        )
        self.conn.commit()
        return "inserted"

    # ------------------------------------------------------------ 감정 저장

    def save_sentiment(
        self, review_id: int, sentiment: str, score: float | None,
        keywords: str | None, model: str,
    ) -> None:
        """감정 분석 결과 저장(재분석 시 덮어쓰기)."""
        self.conn.execute(
            """
            INSERT INTO sentiments (review_id, sentiment, score, keywords, model, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(review_id) DO UPDATE SET
                sentiment = excluded.sentiment,
                score = excluded.score,
                keywords = excluded.keywords,
                model = excluded.model,
                analyzed_at = excluded.analyzed_at
            """,
            (review_id, sentiment, score, keywords, model, now()),
        )
        self.conn.commit()

    def save_extraction(self, record: dict[str, Any]) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO extractions
                (scope_sentiment, scope_product, date_from, date_to, n_reviews,
                 pos_keywords, neg_keywords, summary, complaint_types, suggestions,
                 model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("scope_sentiment"), record.get("scope_product"),
                record.get("date_from"), record.get("date_to"), record.get("n_reviews"),
                record.get("pos_keywords"), record.get("neg_keywords"),
                record.get("summary"), record.get("complaint_types"),
                record.get("suggestions"), record.get("model"), now(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_extraction(self, scope_sentiment: str | None = None) -> sqlite3.Row | None:
        """가장 최근 추출 결과. 리포트에서 AI 인사이트로 재사용한다."""
        if scope_sentiment:
            return self.conn.execute(
                "SELECT * FROM extractions WHERE scope_sentiment = ?"
                " ORDER BY id DESC LIMIT 1",
                (scope_sentiment,),
            ).fetchone()
        return self.conn.execute(
            "SELECT * FROM extractions ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def all_extractions(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM extractions ORDER BY id DESC").fetchall()

    # ---------------------------------------------------------------- 조회

    @staticmethod
    def _build_filters(
        sentiment: str | None = None,
        rating: int | None = None,
        rating_min: int | None = None,
        rating_max: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        product: str | None = None,
        category: str | None = None,
        lang: str | None = None,
        keyword: str | None = None,
        analyzed: bool | None = None,
    ) -> tuple[str, list[Any]]:
        """조회 조건을 WHERE 절과 파라미터로 만든다.

        list/stats/export/dashboard 가 같은 필터 의미를 공유하도록 한 곳에서 만든다.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if sentiment:
            clauses.append("s.sentiment = ?")
            params.append(sentiment)
        if rating is not None:
            clauses.append("c.rating = ?")
            params.append(rating)
        if rating_min is not None:
            clauses.append("c.rating >= ?")
            params.append(rating_min)
        if rating_max is not None:
            clauses.append("c.rating <= ?")
            params.append(rating_max)
        if date_from:
            clauses.append("c.review_date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("c.review_date <= ?")
            params.append(date_to)
        if product:
            clauses.append("c.product = ?")
            params.append(product)
        if category:
            clauses.append("c.category = ?")
            params.append(category)
        if lang:
            clauses.append("c.lang = ?")
            params.append(lang)
        if keyword:
            clauses.append("c.review_text LIKE ?")
            params.append(f"%{keyword}%")
        if analyzed is True:
            clauses.append("s.id IS NOT NULL")
        elif analyzed is False:
            clauses.append("s.id IS NULL")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def query_reviews(
        self,
        limit: int | None = None,
        offset: int = 0,
        sort: str = "date",
        order: str = "desc",
        **filters,
    ) -> list[sqlite3.Row]:
        """조건에 맞는 리뷰를 감정 분석 결과와 함께 가져온다."""
        where, params = self._build_filters(**filters)

        sort_columns = {
            "id": "c.id",
            "date": "c.review_date",
            "rating": "c.rating",
            "score": "s.score",
            "length": "c.text_len",
        }
        sort_sql = sort_columns.get(sort, "c.review_date")
        direction = "DESC" if str(order).lower() == "desc" else "ASC"
        # 정렬 키가 같거나 NULL 일 때 순서가 흔들리지 않도록 id 를 보조 키로 둔다.
        sql = f"{_BASE_SELECT}{where} ORDER BY {sort_sql} {direction}, c.id {direction}"

        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = params + [limit, offset]

        return self.conn.execute(sql, params).fetchall()

    def count_reviews(self, **filters) -> int:
        where, params = self._build_filters(**filters)
        sql = (
            "SELECT COUNT(*) AS n FROM clean_reviews c "
            "LEFT JOIN sentiments s ON s.review_id = c.id" + where
        )
        return int(self.conn.execute(sql, params).fetchone()["n"])

    def get_review(self, review_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            f"{_BASE_SELECT} WHERE c.id = ?", (review_id,)
        ).fetchone()

    def get_raw(self, raw_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM raw_reviews WHERE id = ?", (raw_id,)
        ).fetchone()

    def fetch_for_analysis(
        self, mode: str = "unanalyzed", review_id: int | None = None,
        limit: int | None = None, **filters,
    ) -> list[sqlite3.Row]:
        """감정 분석 대상을 고른다.

        mode: 'unanalyzed'(미분석만) | 'all'(전체 재분석) | 'id'(특정 1건)
        """
        if mode == "id":
            if review_id is None:
                return []
            row = self.get_review(review_id)
            return [row] if row else []

        if mode == "unanalyzed":
            filters["analyzed"] = False

        return self.query_reviews(limit=limit, sort="id", order="asc", **filters)

    # ---------------------------------------------------------------- 집계

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def sentiment_counts(self, **filters) -> dict[str, int]:
        """감정별 건수. 미분석은 제외한다."""
        where, params = self._build_filters(**filters)
        joiner = " AND " if where else " WHERE "
        sql = (
            "SELECT s.sentiment AS sentiment, COUNT(*) AS n "
            "FROM clean_reviews c JOIN sentiments s ON s.review_id = c.id"
            + where
            + f"{joiner}s.sentiment IS NOT NULL GROUP BY s.sentiment"
        )
        rows = self.conn.execute(sql, params).fetchall()
        return {r["sentiment"]: r["n"] for r in rows}

    def rating_counts(self, **filters) -> dict[int, int]:
        """별점별 건수. 별점 없는 리뷰는 제외한다."""
        where, params = self._build_filters(**filters)
        joiner = " AND " if where else " WHERE "
        sql = (
            "SELECT c.rating AS rating, COUNT(*) AS n FROM clean_reviews c "
            "LEFT JOIN sentiments s ON s.review_id = c.id"
            + where + f"{joiner}c.rating IS NOT NULL GROUP BY c.rating ORDER BY c.rating"
        )
        rows = self.conn.execute(sql, params).fetchall()
        return {int(r["rating"]): r["n"] for r in rows}

    def rating_sentiment_matrix(self, **filters) -> dict[tuple[int, str], int]:
        """(별점, 감정) 교차 집계. 별점-감정 상관관계 차트에 쓴다."""
        where, params = self._build_filters(**filters)
        joiner = " AND " if where else " WHERE "
        sql = (
            "SELECT c.rating AS rating, s.sentiment AS sentiment, COUNT(*) AS n "
            "FROM clean_reviews c JOIN sentiments s ON s.review_id = c.id"
            + where
            + f"{joiner}c.rating IS NOT NULL GROUP BY c.rating, s.sentiment"
        )
        rows = self.conn.execute(sql, params).fetchall()
        return {(int(r["rating"]), r["sentiment"]): r["n"] for r in rows}

    def daily_sentiment_counts(self, **filters) -> dict[str, dict[str, int]]:
        """일자별 감정 건수. 시간별 추이 차트에 쓴다."""
        where, params = self._build_filters(**filters)
        joiner = " AND " if where else " WHERE "
        sql = (
            "SELECT c.review_date AS d, s.sentiment AS sentiment, COUNT(*) AS n "
            "FROM clean_reviews c JOIN sentiments s ON s.review_id = c.id"
            + where
            + f"{joiner}c.review_date IS NOT NULL GROUP BY c.review_date, s.sentiment "
            "ORDER BY c.review_date"
        )
        result: dict[str, dict[str, int]] = {}
        for row in self.conn.execute(sql, params).fetchall():
            result.setdefault(row["d"], {})[row["sentiment"]] = row["n"]
        return result

    def product_stats(self, by: str = "product", **filters) -> list[dict[str, Any]]:
        """제품/카테고리별 리뷰 수·평균 별점·감정 분포."""
        column = "c.category" if by == "category" else "c.product"
        where, params = self._build_filters(**filters)
        joiner = " AND " if where else " WHERE "
        sql = f"""
            SELECT {column} AS name,
                   COUNT(*) AS n_reviews,
                   AVG(c.rating) AS avg_rating,
                   AVG(s.score) AS avg_score,
                   SUM(CASE WHEN s.sentiment = 'positive' THEN 1 ELSE 0 END) AS positive,
                   SUM(CASE WHEN s.sentiment = 'neutral'  THEN 1 ELSE 0 END) AS neutral,
                   SUM(CASE WHEN s.sentiment = 'negative' THEN 1 ELSE 0 END) AS negative
              FROM clean_reviews c
              LEFT JOIN sentiments s ON s.review_id = c.id
              {where}{joiner}{column} IS NOT NULL AND {column} != ''
             GROUP BY {column}
             ORDER BY n_reviews DESC
        """
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def counts_summary(self) -> dict[str, int]:
        """파이프라인 단계별 건수. 품질 지표 계산의 기본 재료."""
        return {
            "raw": int(self.scalar("SELECT COUNT(*) FROM raw_reviews") or 0),
            "clean": int(self.scalar("SELECT COUNT(*) FROM clean_reviews") or 0),
            "analyzed": int(self.scalar("SELECT COUNT(*) FROM sentiments") or 0),
            "extractions": int(self.scalar("SELECT COUNT(*) FROM extractions") or 0),
            "imports": int(self.scalar("SELECT COUNT(*) FROM import_log") or 0),
        }

    def import_totals(self) -> dict[str, int]:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(total),0) AS total, COALESCE(SUM(inserted),0) AS inserted,"
            " COALESCE(SUM(skipped),0) AS skipped FROM import_log"
        ).fetchone()
        return {"total": row["total"], "inserted": row["inserted"], "skipped": row["skipped"]}

    def date_range(self, **filters) -> tuple[str | None, str | None]:
        where, params = self._build_filters(**filters)
        joiner = " AND " if where else " WHERE "
        sql = (
            "SELECT MIN(c.review_date) AS a, MAX(c.review_date) AS b FROM clean_reviews c "
            "LEFT JOIN sentiments s ON s.review_id = c.id"
            + where + f"{joiner}c.review_date IS NOT NULL"
        )
        row = self.conn.execute(sql, params).fetchone()
        return (row["a"], row["b"]) if row else (None, None)

    def averages(self, **filters) -> dict[str, float | None]:
        where, params = self._build_filters(**filters)
        sql = (
            "SELECT AVG(c.rating) AS avg_rating, AVG(s.score) AS avg_score "
            "FROM clean_reviews c LEFT JOIN sentiments s ON s.review_id = c.id" + where
        )
        row = self.conn.execute(sql, params).fetchone()
        return {"avg_rating": row["avg_rating"], "avg_score": row["avg_score"]}
