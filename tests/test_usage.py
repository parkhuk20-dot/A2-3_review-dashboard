"""토큰 사용량 · 비용 추적 검증.

단가를 모르는 모델에서 비용을 0 으로 꾸며내지 않는지,
병렬 호출에서 누적이 어긋나지 않는지가 핵심.
"""

from __future__ import annotations

import threading

import pytest

from src.ai.usage import UsageTracker, build_lines, format_cost

PRICING = {"test-model": {"input": 1.0, "output": 2.0}}


class TestUsageTracker:
    def test_호출을_누적한다(self):
        tracker = UsageTracker("test-model", PRICING)
        tracker.add(100, 50)
        tracker.add(200, 100)

        assert tracker.calls == 2
        assert tracker.prompt_tokens == 300
        assert tracker.completion_tokens == 150
        assert tracker.total_tokens == 450

    def test_비용을_입출력_단가로_따로_계산한다(self):
        tracker = UsageTracker("test-model", PRICING)
        tracker.add(1_000_000, 1_000_000)
        # 입력 1M × $1.0 + 출력 1M × $2.0
        assert tracker.cost_usd == pytest.approx(3.0)

    def test_단가를_모르는_모델은_비용이_None(self):
        # 0 으로 꾸며내면 "공짜로 썼다"는 잘못된 인상을 준다.
        tracker = UsageTracker("처음보는-모델", PRICING)
        tracker.add(1000, 500)
        assert tracker.cost_usd is None

    def test_설정_단가가_기본값을_덮어쓴다(self):
        tracker = UsageTracker("gpt-4o-mini", {"gpt-4o-mini": {"input": 99.0, "output": 99.0}})
        tracker.add(1_000_000, 0)
        assert tracker.cost_usd == pytest.approx(99.0)

    def test_설정의_주석_키는_단가표에서_걸러진다(self):
        # config.json 에 `_comment` 를 넣어도 깨지지 않아야 한다.
        tracker = UsageTracker("test-model", {"_comment": "설명", **PRICING})
        tracker.add(1_000_000, 0)
        assert tracker.cost_usd == pytest.approx(1.0)

    def test_None_토큰도_받아낸다(self):
        tracker = UsageTracker("test-model", PRICING)
        tracker.add(None, None)
        assert tracker.total_tokens == 0

    def test_병렬로_더해도_누적이_어긋나지_않는다(self):
        tracker = UsageTracker("test-model", PRICING)

        def hammer():
            for _ in range(200):
                tracker.add(1, 1)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert tracker.calls == 1600
        assert tracker.prompt_tokens == 1600

    def test_기록용_딕셔너리를_만든다(self):
        tracker = UsageTracker("test-model", PRICING)
        tracker.add(100, 50)
        record = tracker.as_record("analyze")

        assert record["command"] == "analyze"
        assert record["model"] == "test-model"
        assert record["calls"] == 1

    def test_호출이_없으면_요약이_그렇게_말한다(self):
        assert UsageTracker("test-model", PRICING).summary() == "API 호출 없음"

    def test_요약에_비용이_들어간다(self):
        tracker = UsageTracker("test-model", PRICING)
        tracker.add(1_000_000, 0)
        assert "$1.0000" in tracker.summary()

    def test_단가를_모르면_요약에_그렇게_적는다(self):
        tracker = UsageTracker("모르는-모델", PRICING)
        tracker.add(100, 50)
        assert "가격 정보 없음" in tracker.summary()


class TestFormatCost:
    @pytest.mark.parametrize("value,expected", [
        (None, "-"),
        (0, "$0"),
        (0.0006, "$0.0006"),   # 작은 값이 0 으로 보이면 안 된다
        (12.345, "$12.35"),
    ])
    def test_자릿수를_상황에_맞게_고른다(self, value, expected):
        assert format_cost(value) == expected


class TestUsagePersistence:
    def test_사용량을_저장하고_합계를_낸다(self, db):
        db.save_usage({"command": "analyze", "model": "m", "calls": 10,
                       "prompt_tokens": 1000, "completion_tokens": 500, "cost_usd": 0.01})
        db.save_usage({"command": "aspects", "model": "m", "calls": 5,
                       "prompt_tokens": 500, "completion_tokens": 200, "cost_usd": 0.005})

        totals = db.usage_totals()
        assert totals["calls"] == 15
        assert totals["total_tokens"] == 2200
        assert totals["cost_usd"] == pytest.approx(0.015)
        assert totals["runs"] == 2

    def test_호출이_0이면_기록하지_않는다(self):
        # mock 실행마다 빈 행이 쌓이면 누적 조회가 지저분해진다.
        from src.db import Database
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(pathlib.Path(tmp) / "t.db")
            assert database.save_usage({"command": "analyze", "calls": 0}) is None
            assert database.usage_totals()["runs"] == 0
            database.close()

    def test_명령별로_묶어_보여준다(self, db):
        for _ in range(2):
            db.save_usage({"command": "analyze", "model": "m", "calls": 10,
                           "prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.01})
        db.save_usage({"command": "extract", "model": "m", "calls": 1,
                       "prompt_tokens": 900, "completion_tokens": 300, "cost_usd": 0.02})

        by_command = {row["command"]: row for row in db.usage_by_command()}
        assert by_command["analyze"]["calls"] == 20
        assert by_command["analyze"]["runs"] == 2
        assert by_command["extract"]["calls"] == 1

    def test_빈_DB의_합계는_0(self, db):
        totals = db.usage_totals()
        assert totals["calls"] == 0
        assert totals["total_tokens"] == 0


class TestBuildLines:
    def test_기록이_없으면_안내한다(self):
        totals = {"calls": 0, "runs": 0, "prompt_tokens": 0,
                  "completion_tokens": 0, "total_tokens": 0, "cost_usd": None}
        assert any("사용량이 없습니다" in line for line in build_lines(totals, []))

    def test_합계와_명령별_내역을_함께_낸다(self):
        totals = {"calls": 100, "runs": 3, "prompt_tokens": 8000,
                  "completion_tokens": 2000, "total_tokens": 10000, "cost_usd": 0.0132}
        by_command = [{"command": "analyze", "model": "gpt-4o-mini", "calls": 90,
                       "prompt_tokens": 7000, "completion_tokens": 1800, "cost_usd": 0.012}]

        text = "\n".join(build_lines(totals, by_command))
        assert "100회" in text
        assert "$0.0132" in text
        assert "analyze" in text
