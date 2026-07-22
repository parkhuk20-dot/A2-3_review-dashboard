"""지수 백오프 재시도 데코레이터. AI API 호출처럼 일시적 실패가 있는 곳에 쓴다."""

from __future__ import annotations

import functools
import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry(
    max_attempts: int = 3,
    backoff_base: float = 1.6,
    initial_delay: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """실패 시 지수 백오프로 재시도한다.

    마지막 시도까지 실패하면 예외를 그대로 올려보내, 호출한 쪽에서
    "로깅 후 스킵" 여부를 결정하게 한다.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            delay = initial_delay
            last_exc: BaseException | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    # 동시 재시도가 몰리지 않도록 약간의 지터를 준다.
                    sleep_for = delay + random.uniform(0, 0.3)
                    logger.warning(
                        "%s 실패(%d/%d): %s — %.1f초 후 재시도",
                        func.__name__, attempt, max_attempts, exc, sleep_for,
                    )
                    time.sleep(sleep_for)
                    delay *= backoff_base

            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
