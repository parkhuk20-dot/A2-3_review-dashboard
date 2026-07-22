"""matplotlib 차트 생성.

필수 3종
    1. 감정 분포          (막대 + 비율 라벨)
    2. 시간별 감정 추이    (일자별 선그래프)
    3. 별점별 감정 분포    (누적 막대 — 별점과 감정의 상관관계)
보너스
    4. 제품별 비교        (그룹 막대)

한글 라벨이 깨지지 않도록 OS 별 폰트를 자동 탐색한다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 화면 없는 환경에서도 PNG 저장이 되도록
import matplotlib.pyplot as plt
from matplotlib import font_manager

logger = logging.getLogger(__name__)

SENTIMENT_ORDER = ("positive", "neutral", "negative")
SENTIMENT_KO = {"positive": "긍정", "neutral": "중립", "negative": "부정"}

# 우선순위대로 탐색: macOS → Windows → Linux(나눔) 순
_FONT_CANDIDATES = [
    "AppleGothic", "Apple SD Gothic Neo", "Malgun Gothic",
    "NanumGothic", "NanumBarunGothic", "Noto Sans CJK KR", "Noto Sans KR",
]

_font_ready = False


def setup_korean_font() -> str | None:
    """한글 폰트를 찾아 matplotlib 기본값으로 설정한다."""
    global _font_ready
    if _font_ready:
        return matplotlib.rcParams["font.family"][0]

    available = {f.name for f in font_manager.fontManager.ttflist}
    for candidate in _FONT_CANDIDATES:
        if candidate in available:
            matplotlib.rcParams["font.family"] = candidate
            matplotlib.rcParams["axes.unicode_minus"] = False  # 음수 기호 깨짐 방지
            _font_ready = True
            logger.debug("한글 폰트 적용: %s", candidate)
            return candidate

    logger.warning(
        "한글 폰트를 찾지 못했습니다. 차트의 한글이 깨질 수 있습니다. "
        "(macOS: AppleGothic 기본 제공 / Linux: fonts-nanum 설치 권장)"
    )
    matplotlib.rcParams["axes.unicode_minus"] = False
    _font_ready = True
    return None


def _palette(cfg: dict[str, Any]) -> dict[str, str]:
    return cfg.get("viz", {}).get(
        "palette", {"positive": "#2E86DE", "neutral": "#95A5A6", "negative": "#E74C3C"}
    )


def _save(fig, path: Path, dpi: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    logger.info("차트 저장: %s", path)
    return path


def chart_sentiment_distribution(
    counts: dict[str, int], out_dir: Path, cfg: dict[str, Any]
) -> Path | None:
    """① 감정 분포 막대 차트."""
    if not counts:
        logger.warning("감정 분포 차트: 분석된 리뷰가 없어 건너뜁니다.")
        return None

    setup_korean_font()
    palette = _palette(cfg)
    viz = cfg.get("viz", {})

    labels = [SENTIMENT_KO[s] for s in SENTIMENT_ORDER]
    values = [counts.get(s, 0) for s in SENTIMENT_ORDER]
    colors = [palette.get(s, "#888888") for s in SENTIMENT_ORDER]
    total = sum(values) or 1

    fig, ax = plt.subplots(figsize=viz.get("figsize", [9, 5.5]))
    bars = ax.bar(labels, values, color=colors, width=0.55)

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height(),
            f"{value}건\n({value / total * 100:.1f}%)",
            ha="center", va="bottom", fontsize=11,
        )

    ax.set_title("감정 분포", fontsize=15, pad=15)
    ax.set_ylabel("리뷰 수")
    ax.set_ylim(0, max(values) * 1.2 if max(values) else 1)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)

    return _save(fig, out_dir / "sentiment_distribution.png", viz.get("dpi", 130))


def aggregate_by_week(daily: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """일자별 집계를 주 단위(해당 주 월요일 기준)로 묶는다.

    하루 몇 건 수준이면 일자별 선그래프는 톱니처럼 튀어 추세가 안 보인다.
    """
    weekly: dict[str, dict[str, int]] = {}
    for date_str, counts in daily.items():
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        monday = date.fromordinal(date.toordinal() - date.weekday()).strftime("%Y-%m-%d")
        bucket = weekly.setdefault(monday, {})
        for sentiment, count in counts.items():
            bucket[sentiment] = bucket.get(sentiment, 0) + count
    return weekly


def chart_sentiment_trend(
    daily: dict[str, dict[str, int]], out_dir: Path, cfg: dict[str, Any],
    unit: str | None = None,
) -> Path | None:
    """② 시간별 감정 추이 선그래프. unit 은 'day' 또는 'week'."""
    if not daily:
        logger.warning("감정 추이 차트: 날짜가 있는 분석 결과가 없어 건너뜁니다.")
        return None

    setup_korean_font()
    palette = _palette(cfg)
    viz = cfg.get("viz", {})

    unit = unit or cfg.get("report", {}).get("trend_unit", "day")
    if unit == "week":
        daily = aggregate_by_week(daily)

    dates = sorted(daily.keys())
    fig, ax = plt.subplots(figsize=viz.get("figsize", [9, 5.5]))

    for sentiment in SENTIMENT_ORDER:
        series = [daily[d].get(sentiment, 0) for d in dates]
        ax.plot(
            dates, series, marker="o", markersize=4, linewidth=1.8,
            label=SENTIMENT_KO[sentiment], color=palette.get(sentiment, "#888888"),
        )

    ax.set_title(f"시간별 감정 추이 ({'주' if unit == 'week' else '일'} 단위)", fontsize=15, pad=15)
    ax.set_xlabel("주 시작일(월요일)" if unit == "week" else "작성일")
    ax.set_ylabel("리뷰 수")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.set_axisbelow(True)

    # 날짜가 많으면 라벨이 겹치므로 일정 간격으로만 표시한다.
    step = max(1, len(dates) // 12)
    ax.set_xticks(range(0, len(dates), step))
    ax.set_xticklabels([dates[i][5:] for i in range(0, len(dates), step)], rotation=45, ha="right")

    return _save(fig, out_dir / "sentiment_trend.png", viz.get("dpi", 130))


def chart_rating_sentiment(
    matrix: dict[tuple[int, str], int], out_dir: Path, cfg: dict[str, Any]
) -> Path | None:
    """③ 별점별 감정 분포 누적 막대 — 별점과 감정의 상관관계."""
    if not matrix:
        logger.warning("별점-감정 차트: 별점과 분석 결과가 모두 있는 리뷰가 없어 건너뜁니다.")
        return None

    setup_korean_font()
    palette = _palette(cfg)
    viz = cfg.get("viz", {})

    ratings = sorted({rating for rating, _ in matrix})
    labels = [f"★{r}" for r in ratings]

    fig, ax = plt.subplots(figsize=viz.get("figsize", [9, 5.5]))
    bottom = [0] * len(ratings)

    for sentiment in SENTIMENT_ORDER:
        values = [matrix.get((r, sentiment), 0) for r in ratings]
        ax.bar(
            labels, values, bottom=bottom, width=0.6,
            label=SENTIMENT_KO[sentiment], color=palette.get(sentiment, "#888888"),
        )
        bottom = [b + v for b, v in zip(bottom, values)]

    for index, total in enumerate(bottom):
        if total:
            ax.text(index, total, f"{total}건", ha="center", va="bottom", fontsize=10)

    ax.set_title("별점별 감정 분포", fontsize=15, pad=15)
    ax.set_xlabel("별점")
    ax.set_ylabel("리뷰 수")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)

    return _save(fig, out_dir / "rating_sentiment_matrix.png", viz.get("dpi", 130))


def chart_product_comparison(
    rows: list[dict[str, Any]], out_dir: Path, cfg: dict[str, Any],
    by: str = "product",
) -> Path | None:
    """④ [보너스] 제품·카테고리별 감정 비교 그룹 막대 + 평균 별점 선."""
    rows = [r for r in rows if r.get("name")]
    if not rows:
        logger.warning("비교 차트: 제품/카테고리 정보가 있는 리뷰가 없어 건너뜁니다.")
        return None

    setup_korean_font()
    palette = _palette(cfg)
    viz = cfg.get("viz", {})

    names = [r["name"] for r in rows]
    positions = range(len(names))
    width = 0.26

    fig, ax = plt.subplots(figsize=viz.get("figsize", [10, 5.5]))
    for offset, sentiment in zip((-width, 0, width), SENTIMENT_ORDER):
        values = [r.get(sentiment) or 0 for r in rows]
        ax.bar(
            [p + offset for p in positions], values, width=width,
            label=SENTIMENT_KO[sentiment], color=palette.get(sentiment, "#888888"),
        )

    ax.set_title(f"{'카테고리' if by == 'category' else '제품'}별 감정 비교", fontsize=15, pad=15)
    ax.set_ylabel("리뷰 수")
    ax.set_xticks(list(positions))
    ax.set_xticklabels(names, rotation=15, ha="right")
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)

    # 평균 별점은 스케일이 달라 보조 축에 얹는다.
    ax2 = ax.twinx()
    avg_ratings = [r.get("avg_rating") or 0 for r in rows]
    ax2.plot(list(positions), avg_ratings, color="#F39C12", marker="D",
             linewidth=2, label="평균 별점")
    ax2.set_ylabel("평균 별점")
    ax2.set_ylim(0, 5.5)

    handles1, labels1 = ax.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(handles1 + handles2, labels1 + labels2, loc="upper right")

    suffix = "category" if by == "category" else "product"
    return _save(fig, out_dir / f"{suffix}_comparison.png", viz.get("dpi", 130))


def generate_all(
    db, cfg: dict[str, Any], out_dir: Path, trend_unit: str | None = None, **filters
) -> list[Path]:
    """대시보드용 차트를 한 번에 만든다. 실패한 차트는 건너뛰고 계속 진행한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    charts: list[Path] = []

    builders = [
        ("감정 분포", lambda: chart_sentiment_distribution(
            db.sentiment_counts(**filters), out_dir, cfg)),
        ("시간별 추이", lambda: chart_sentiment_trend(
            db.daily_sentiment_counts(**filters), out_dir, cfg, unit=trend_unit)),
        ("별점-감정", lambda: chart_rating_sentiment(
            db.rating_sentiment_matrix(**filters), out_dir, cfg)),
        ("제품 비교", lambda: chart_product_comparison(
            db.product_stats(by="product", **filters), out_dir, cfg, by="product")),
    ]

    for name, builder in builders:
        try:
            path = builder()
            if path:
                charts.append(path)
        except Exception as exc:  # 차트 하나가 실패해도 리포트는 나와야 한다.
            logger.error("%s 차트 생성 실패: %s", name, exc)
            logger.debug("상세 오류", exc_info=True)

    return charts


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
