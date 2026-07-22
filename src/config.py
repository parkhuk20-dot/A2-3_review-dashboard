"""설정 로드.

우선순위: 환경변수 > .env 파일 > config.json > 내장 기본값
API 키는 config.json 에 두지 않고 환경변수/.env 로만 받는다.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

# config.json 이 없거나 일부 키가 빠져도 동작하도록 내장 기본값을 둔다.
DEFAULTS: dict[str, Any] = {
    "import": {
        "default_encoding": "utf-8-sig",
        "column_aliases": {
            "review_text": ["review_text", "review", "text", "content", "body",
                            "리뷰", "리뷰내용", "내용", "후기"],
            "rating": ["rating", "score", "star", "stars", "별점", "평점", "점수"],
            "review_date": ["review_date", "date", "created_at", "written_at",
                            "작성일", "날짜", "등록일"],
            "product": ["product", "product_name", "item", "제품", "제품명", "상품", "상품명"],
            "category": ["category", "cat", "카테고리", "분류"],
        },
    },
    "clean": {
        "dedup_policy": "skip",
        "min_length": 10,
        "rating_min": 1,
        "rating_max": 5,
        "strip_html": True,
    },
    "ai": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "batch_size": 10,
        "max_reviews_per_extraction": 80,
        "review_chars_for_extraction": 300,
    },
    "report": {"top_n": 5, "trend_unit": "day"},
    "alert": {"days": 7, "threshold": 1.5, "min_reviews": 5},
    "viz": {
        "dpi": 130,
        "figsize": [9, 5.5],
        "palette": {"positive": "#2E86DE", "neutral": "#95A5A6", "negative": "#E74C3C"},
    },
    "paths": {
        "db": "data/reviews.db",
        "charts": "output/charts",
        "reports": "output/reports",
        "exports": "output/exports",
        "log": "logs/dashboard.log",
    },
}


def load_dotenv(path: Path | None = None) -> int:
    """프로젝트 루트의 .env 를 읽어 환경변수로 올린다.

    셸에서 export 한 값이 이미 있으면 그쪽을 우선한다(기존 환경 존중).
    python-dotenv 의존성을 추가하지 않으려고 최소 기능만 직접 구현했다.
    """
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return 0

    loaded = 0
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def _deep_merge(base: dict, override: dict) -> dict:
    """중첩 딕셔너리 병합. override 쪽 값이 이긴다."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """설정을 로드해 딕셔너리로 반환한다."""
    load_dotenv()

    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    config = copy.deepcopy(DEFAULTS)

    if config_path.exists():
        try:
            user_config = json.loads(config_path.read_text(encoding="utf-8"))
            user_config.pop("_comment", None)
            config = _deep_merge(config, user_config)
        except json.JSONDecodeError as exc:
            logger.warning("설정 파일 파싱 실패(%s), 기본값으로 진행합니다: %s", config_path, exc)
    else:
        logger.warning("설정 파일이 없어 기본값으로 진행합니다: %s", config_path)

    # API 키는 설정 파일이 아니라 환경변수에서만 읽는다.
    config["api_key"] = os.environ.get("OPENAI_API_KEY", "")
    config["api_base_url"] = os.environ.get("OPENAI_BASE_URL", "")
    config["_config_path"] = str(config_path)
    return config


def resolve_path(relative: str) -> Path:
    """설정의 상대 경로를 프로젝트 루트 기준 절대 경로로 바꾼다.

    어느 디렉터리에서 실행하든 같은 DB·출력 위치를 쓰게 한다.
    """
    p = Path(relative)
    return p if p.is_absolute() else PROJECT_ROOT / p


def ensure_dirs(config: dict[str, Any]) -> None:
    """설정에 적힌 출력 디렉터리를 미리 만들어 둔다."""
    paths = config.get("paths", {})
    for key in ("db", "log"):
        if paths.get(key):
            resolve_path(paths[key]).parent.mkdir(parents=True, exist_ok=True)
    for key in ("charts", "reports", "exports"):
        if paths.get(key):
            resolve_path(paths[key]).mkdir(parents=True, exist_ok=True)
