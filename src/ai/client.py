"""OpenAI 호출 래퍼.

- JSON 응답을 강제해 자연어 파싱 실패 위험을 없앤다
- 일시적 오류는 지수 백오프로 재시도한다
- `--mock` 모드에서는 API 없이 규칙 기반 결과를 돌려준다
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.retry import retry

logger = logging.getLogger(__name__)


class AIError(RuntimeError):
    """AI 호출 실패. 호출한 쪽에서 '로깅 후 스킵'을 결정한다."""


class AIClient:
    """감정 분석·키워드 추출이 공유하는 얇은 래퍼."""

    def __init__(self, cfg: dict[str, Any], mock: bool = False):
        ai_cfg = cfg.get("ai", {})
        self.model = ai_cfg.get("model", "gpt-4o-mini")
        self.temperature = ai_cfg.get("temperature", 0.2)
        self.mock = mock
        self._client = None

        if self.mock:
            logger.info("mock 모드: AI API 를 호출하지 않고 규칙 기반 결과를 사용합니다.")
            return

        api_key = cfg.get("api_key", "")
        if not api_key:
            raise AIError(
                "OPENAI_API_KEY 가 없습니다. .env 파일에 키를 넣거나 "
                "--mock 옵션으로 실행하세요."
            )

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIError("openai 패키지가 필요합니다: pip install openai") from exc

        kwargs: dict[str, Any] = {"api_key": api_key}
        if cfg.get("api_base_url"):
            kwargs["base_url"] = cfg["api_base_url"]
        self._client = OpenAI(**kwargs)

    @property
    def model_name(self) -> str:
        return "mock" if self.mock else self.model

    @retry(max_attempts=3, backoff_base=1.6, initial_delay=1.0)
    def _call(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            response_format={"type": "json_object"},  # JSON 응답 강제
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """JSON 응답을 받아 딕셔너리로 돌려준다."""
        if self.mock:
            raise AIError("mock 모드에서는 complete_json 을 쓰지 않습니다.")

        try:
            raw = self._call(system, user)
        except Exception as exc:
            raise AIError(f"AI 호출 실패: {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            # JSON 강제를 켜도 드물게 앞뒤 텍스트가 붙는 경우가 있어 한 번 더 건진다.
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end > start:
                try:
                    return json.loads(raw[start:end + 1])
                except json.JSONDecodeError:
                    pass
            raise AIError(f"AI 응답을 JSON 으로 해석하지 못했습니다: {raw[:200]!r}") from exc
