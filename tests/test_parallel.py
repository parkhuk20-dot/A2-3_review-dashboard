"""병렬 호출 헬퍼 검증.

핵심은 세 가지 — 전부 처리하는가, 일부 실패가 나머지를 막지 않는가,
그리고 순차 모드에서는 순서가 보존되는가.
"""

from __future__ import annotations

import threading
import time

import pytest

from src.ai.parallel import map_concurrent, resolve_workers


class TestResolveWorkers:
    def test_설정값을_읽는다(self):
        assert resolve_workers({"ai": {"concurrency": 8}}) == 8

    def test_인자가_설정을_이긴다(self):
        assert resolve_workers({"ai": {"concurrency": 8}}, override=2) == 2

    def test_설정이_없으면_순차(self):
        assert resolve_workers({}) == 1

    @pytest.mark.parametrize("value", [0, -5])
    def test_0이하는_1로_올린다(self, value):
        assert resolve_workers({}, override=value) == 1


class TestMapConcurrent:
    @pytest.mark.parametrize("workers", [1, 4])
    def test_모든_항목을_처리한다(self, workers):
        rows = list(range(20))
        results = {row: value for row, value, _ in
                   map_concurrent(rows, lambda r: r * 2, workers=workers)}
        assert results == {r: r * 2 for r in rows}

    @pytest.mark.parametrize("workers", [1, 4])
    def test_일부가_실패해도_나머지는_처리된다(self, workers):
        def work(row):
            if row % 3 == 0:
                raise ValueError(f"실패 {row}")
            return row

        ok, failed = [], []
        for row, value, error in map_concurrent(range(12), work, workers=workers):
            (failed if error else ok).append(row)

        assert len(ok) == 8
        assert len(failed) == 4

    @pytest.mark.parametrize("workers", [1, 4])
    def test_예외를_삼키지_않고_그대로_넘긴다(self, workers):
        def work(row):
            raise ValueError("터짐")

        _, value, error = next(iter(map_concurrent([1], work, workers=workers)))
        assert value is None
        assert isinstance(error, ValueError)
        assert "터짐" in str(error)

    def test_순차_모드는_입력_순서를_보존한다(self):
        # mock 실행이나 디버깅에서 재현성이 필요하다.
        rows = list(range(10))
        order = [row for row, _, _ in map_concurrent(rows, lambda r: r, workers=1)]
        assert order == rows

    def test_빈_입력은_아무것도_내놓지_않는다(self):
        assert list(map_concurrent([], lambda r: r, workers=4)) == []

    def test_병렬_모드는_실제로_동시에_실행된다(self):
        # 0.1초 걸리는 작업 8개를 8 워커로 돌리면 순차(0.8초)보다 확실히 빨라야 한다.
        def slow(row):
            time.sleep(0.1)
            return row

        start = time.perf_counter()
        list(map_concurrent(range(8), slow, workers=8))
        elapsed = time.perf_counter() - start

        assert elapsed < 0.4, f"동시 실행이 아닌 것으로 보입니다 ({elapsed:.2f}초)"

    def test_동시_실행_수를_넘지_않는다(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def work(row):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return row

        list(map_concurrent(range(20), work, workers=4))
        assert peak <= 4
