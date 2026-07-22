"""[보너스] 제품 · 카테고리별 비교 분석.

같은 기간 안에서 어떤 제품이 상대적으로 문제인지 한눈에 보이게 한다.
리뷰 수가 적은 항목은 비율이 크게 흔들리므로 건수를 함께 보여준다.
"""

from __future__ import annotations

import logging
from typing import Any

from src import config as config_module
from src import visualize
from src.db import Database
from src.viewer import display_width, pad

logger = logging.getLogger(__name__)


def build_comparison(
    db: Database, by: str = "product", products: list[str] | None = None, **filters,
) -> list[dict[str, Any]]:
    """비교표에 필요한 수치를 만든다."""
    rows = db.product_stats(by=by, **filters)

    if products:
        wanted = {p.strip() for p in products if p.strip()}
        rows = [r for r in rows if r["name"] in wanted]

    for row in rows:
        analyzed = (row["positive"] or 0) + (row["neutral"] or 0) + (row["negative"] or 0)
        row["analyzed"] = analyzed
        row["positive_rate"] = (row["positive"] or 0) / analyzed * 100 if analyzed else 0.0
        row["negative_rate"] = (row["negative"] or 0) / analyzed * 100 if analyzed else 0.0

    # 부정 비율이 높은 순 — 문제가 큰 항목을 위로 올린다.
    rows.sort(key=lambda r: r["negative_rate"], reverse=True)
    return rows


def print_comparison(rows: list[dict[str, Any]], by: str) -> None:
    label = "카테고리" if by == "category" else "제품"
    print()
    print(f"=== {label}별 비교 분석 ===")

    if not rows:
        print(f"{label} 정보가 있는 리뷰가 없습니다.\n")
        return

    name_width = max(display_width(str(r["name"])) for r in rows)
    name_width = max(name_width, display_width(label))

    header = (f"{pad(label, name_width)} | 리뷰수 | 평균별점 | 긍정 | 중립 | 부정 | "
              f"긍정률 | 부정률")
    print(header)
    print("-" * (display_width(header) + 2))

    for row in rows:
        avg = f"{row['avg_rating']:.2f}" if row["avg_rating"] is not None else "  -  "
        print(
            f"{pad(str(row['name']), name_width)} | "
            f"{row['n_reviews']:>5}건 | {avg:>8} | "
            f"{row['positive'] or 0:>4} | {row['neutral'] or 0:>4} | {row['negative'] or 0:>4} | "
            f"{row['positive_rate']:>5.1f}% | {row['negative_rate']:>5.1f}%"
        )

    print()

    worst = rows[0]
    best = min(rows, key=lambda r: r["negative_rate"])
    if worst["name"] != best["name"]:
        print(f"[해석] 부정 비율이 가장 높은 {label}은 '{worst['name']}'"
              f"({worst['negative_rate']:.1f}%), 가장 낮은 {label}은 '{best['name']}'"
              f"({best['negative_rate']:.1f}%)입니다.")
        gap = worst["negative_rate"] - best["negative_rate"]
        print(f"       두 {label} 간 부정 비율 차이는 {gap:.1f}%p 입니다.")
    print()


# --------------------------------------------------------------------- CLI

def cmd_compare(args, cfg: dict[str, Any]) -> int:
    filters: dict[str, Any] = {}
    for attr in ("date_from", "date_to"):
        value = getattr(args, attr, None)
        if value:
            filters[attr] = value

    products = args.products.split(",") if args.products else None

    db_path = config_module.resolve_path(cfg["paths"]["db"])
    with Database(db_path) as db:
        rows = build_comparison(db, by=args.by, products=products, **filters)
        print_comparison(rows, args.by)

        if args.chart and rows:
            charts_dir = config_module.resolve_path(cfg["paths"]["charts"])
            path = visualize.chart_product_comparison(rows, charts_dir, cfg, by=args.by)
            if path:
                logger.info("비교 차트 저장: %s", path)

    return 0
