"""AI 호출 병렬 실행 헬퍼.

리뷰당 1회 호출은 건수에 비례해 시간이 늘어난다(100건에 수 분).
호출 대부분이 응답 대기라 CPU 는 놀고 있으므로, 스레드로 겹쳐 부르면 크게 줄어든다.

**DB 쓰기는 호출한 쪽(주 스레드)에서 한다.** sqlite3 연결은 기본적으로 스레드 간
공유가 막혀 있고, 굳이 풀어도 동시 쓰기는 이득이 없다. 느린 건 네트워크지 DB 가 아니다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterator, Sequence

logger = logging.getLogger(__name__)


def resolve_workers(cfg: dict[str, Any], override: int | None = None) -> int:
    """동시 실행 수를 정한다. 1 이면 순차 실행."""
    if override is not None:
        return max(1, override)
    return max(1, int(cfg.get("ai", {}).get("concurrency", 1)))


def map_concurrent(
    rows: Sequence[Any],
    work: Callable[[Any], Any],
    workers: int = 1,
) -> Iterator[tuple[Any, Any, BaseException | None]]:
    """각 행에 `work` 를 적용하고 `(행, 결과, 예외)` 를 완료 순서대로 내놓는다.

    예외를 삼키지 않고 그대로 넘겨, 호출한 쪽이 '로깅 후 스킵'을 결정하게 한다.
    workers <= 1 이면 스레드를 쓰지 않아 순서가 입력 순서 그대로 유지된다
    (mock 실행이나 디버깅에서 재현성이 필요할 때 유용).
    """
    if not rows:
        return

    if workers <= 1:
        for row in rows:
            try:
                yield row, work(row), None
            except Exception as exc:  # 개별 실패가 전체를 멈추지 않게 한다
                yield row, None, exc
        return

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, row): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            try:
                yield row, future.result(), None
            except Exception as exc:
                yield row, None, exc
