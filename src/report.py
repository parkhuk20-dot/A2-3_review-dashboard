"""종합 대시보드 리포트 생성.

구성
    핵심 지표 → 품질 지표 5종 → TOP N 집계 3종 → AI 추출 결과 → 생성된 차트 목록
콘솔로 출력하면서 동시에 MD/TXT 파일로도 저장한다.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from src import alert as alert_module
from src import config as config_module
from src import visualize
from src.db import Database
from src.viewer import SENTIMENT_KO, build_stats, stars

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ 집계

def keyword_counter(db: Database, sentiment: str, top_n: int, **filters) -> list[tuple[str, int]]:
    """감정별 리뷰 키워드 빈도 TOP N.

    analyze 단계에서 리뷰마다 저장해 둔 keywords 를 모아 센다.
    """
    rows = db.query_reviews(sentiment=sentiment, analyzed=True, **filters)
    counter: Counter[str] = Counter()
    for row in rows:
        if not row["keywords"]:
            continue
        for keyword in str(row["keywords"]).split(","):
            keyword = keyword.strip()
            if keyword:
                counter[keyword] += 1
    return counter.most_common(top_n)


def quality_metrics(db: Database, stats: dict[str, Any], **filters) -> dict[str, Any]:
    """품질 지표 5종.

    - 정제 통과율      : 원본 중 정제를 통과한 비율
    - 감정 분석 완료율 : 정제 데이터 중 분석이 끝난 비율
    - 별점-감정 일치율 : ★4~5=긍정 / ★1~2=부정 / ★3=중립 기준 일치 비율
    - 평균 신뢰도      : AI 가 스스로 매긴 확신 정도의 평균
    - 결측률           : 별점·날짜가 비어 있는 비율
    """
    raw_total = stats["raw_total"]
    clean_total = stats["clean_total"]

    # 별점과 감정이 같은 방향인지 — 과제가 요구한 '별점과 감정의 상관관계'를 수치로.
    rows = db.query_reviews(analyzed=True, **filters)
    comparable = agreed = 0
    for row in rows:
        if row["rating"] is None or not row["sentiment"]:
            continue
        expected = ("negative" if row["rating"] <= 2
                    else "positive" if row["rating"] >= 4 else "neutral")
        comparable += 1
        if expected == row["sentiment"]:
            agreed += 1

    total = stats["total"] or 1
    missing_rating = total - stats["rated_total"]
    no_date = db.scalar("SELECT COUNT(*) FROM clean_reviews WHERE review_date IS NULL") or 0

    return {
        "clean_rate": (clean_total / raw_total * 100) if raw_total else 0.0,
        "dropped": max(0, raw_total - clean_total),
        "analyzed_rate": stats["analyzed_rate"],
        "agreement_rate": (agreed / comparable * 100) if comparable else 0.0,
        "agreement_base": comparable,
        "avg_score": stats["avg_score"],
        "missing_rating_rate": (missing_rating / total * 100),
        "missing_date": no_date,
    }


def build_report(
    db: Database, cfg: dict[str, Any], charts: list[Path],
    top_n: int | None = None, **filters,
) -> str:
    """마크다운 형식의 리포트 본문을 만든다."""
    top_n = top_n or cfg.get("report", {}).get("top_n", 5)
    stats = build_stats(db, **filters)
    metrics = quality_metrics(db, stats, **filters)
    extraction = db.latest_extraction()

    lines: list[str] = []
    add = lines.append

    add("# 고객 리뷰 감정 분석 대시보드")
    add("")
    add(f"- 생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if stats["date_from"]:
        add(f"- 분석 기간: {stats['date_from']} ~ {stats['date_to']}")
    if filters:
        described = ", ".join(f"{k}={v}" for k, v in filters.items())
        add(f"- 적용 필터: {described}")
    add("")

    # ------------------------------------------------------------ 핵심 지표
    positive = stats["sentiment_counts"].get("positive", 0)
    positive_rate = (positive / stats["analyzed"] * 100) if stats["analyzed"] else 0.0

    add("## 핵심 지표")
    add("")
    add("| 지표 | 값 |")
    add("|------|-----|")
    add(f"| 총 리뷰 수 | {stats['total']}건 |")
    add(f"| 분석 완료율 | {stats['analyzed_rate']:.1f}% ({stats['analyzed']}건) |")
    add(f"| 긍정 비율 | {positive_rate:.1f}% |")
    add(f"| 평균 별점 | {stats['avg_rating']:.2f} |" if stats["avg_rating"] is not None
        else "| 평균 별점 | - |")
    add(f"| 평균 감정 점수 | {stats['avg_score']:.2f} |" if stats["avg_score"] is not None
        else "| 평균 감정 점수 | - |")
    add("")

    # ----------------------------------------------------------- 감정/별점 분포
    add("## 감정 분포")
    add("")
    if stats["analyzed"]:
        for label in ("positive", "neutral", "negative"):
            count = stats["sentiment_counts"].get(label, 0)
            ratio = count / stats["analyzed"] * 100
            add(f"- {SENTIMENT_KO[label]}: {count}건 ({ratio:.1f}%)")
    else:
        add("- 분석된 리뷰가 없습니다.")
    add("")

    add("## 별점 분포")
    add("")
    if stats["rated_total"]:
        for rating in range(5, 0, -1):
            count = stats["rating_counts"].get(rating, 0)
            ratio = count / stats["rated_total"] * 100
            add(f"- {stars(rating)} ({rating}점): {count}건 ({ratio:.1f}%)")
    else:
        add("- 별점 정보가 있는 리뷰가 없습니다.")
    add("")

    # -------------------------------------------------- 감정 변화 알림(보너스)
    add("## 감정 변화 알림")
    add("")
    spike = alert_module.detect_spike(db, cfg, **filters)
    for line in alert_module.format_alert(spike):
        add(line)
    add("")

    # ------------------------------------------------------------ 품질 지표
    add("## 품질 지표")
    add("")
    add(f"- 정제 통과율: {metrics['clean_rate']:.1f}% "
        f"(원본 {stats['raw_total']}건 → 정제 {stats['clean_total']}건, 제외 {metrics['dropped']}건)")
    add(f"- 감정 분석 완료율: {metrics['analyzed_rate']:.1f}%")
    add(f"- 별점–감정 일치율: {metrics['agreement_rate']:.1f}% "
        f"(비교 가능 {metrics['agreement_base']}건, ★4~5=긍정 / ★3=중립 / ★1~2=부정 기준)")
    add(f"- 평균 신뢰도 점수: {metrics['avg_score']:.2f}" if metrics["avg_score"] is not None
        else "- 평균 신뢰도 점수: -")
    add(f"- 결측률: 별점 없음 {metrics['missing_rating_rate']:.1f}%, "
        f"작성일 없음 {metrics['missing_date']}건")
    add("")

    # -------------------------------------------------------------- TOP N
    positive_keywords = keyword_counter(db, "positive", top_n, **filters)
    negative_keywords = keyword_counter(db, "negative", top_n, **filters)

    add(f"## TOP {top_n} 긍정 키워드")
    add("")
    if positive_keywords:
        for index, (keyword, count) in enumerate(positive_keywords, start=1):
            add(f"{index}. {keyword} ({count}회)")
    else:
        add("- 집계된 키워드가 없습니다.")
    add("")

    add(f"## TOP {top_n} 부정 키워드")
    add("")
    if negative_keywords:
        for index, (keyword, count) in enumerate(negative_keywords, start=1):
            add(f"{index}. {keyword} ({count}회)")
    else:
        add("- 집계된 키워드가 없습니다.")
    add("")

    product_rows = db.product_stats(by="product", **filters)
    rated_products = [p for p in product_rows if p.get("avg_rating") is not None]
    rated_products.sort(key=lambda p: p["avg_rating"], reverse=True)

    add(f"## 제품별 평균 별점 TOP {top_n}")
    add("")
    if rated_products:
        add("| 순위 | 제품 | 리뷰 수 | 평균 별점 | 긍정 | 중립 | 부정 |")
        add("|------|------|---------|-----------|------|------|------|")
        for index, row in enumerate(rated_products[:top_n], start=1):
            add(f"| {index} | {row['name']} | {row['n_reviews']}건 | "
                f"{row['avg_rating']:.2f} | {row['positive'] or 0} | "
                f"{row['neutral'] or 0} | {row['negative'] or 0} |")
    else:
        add("- 제품 정보가 있는 리뷰가 없습니다.")
    add("")

    # ------------------------------------------------------- AI 추출 결과
    add("## AI 인사이트")
    add("")
    if extraction is None:
        add("- 추출 결과가 없습니다. `python main.py extract` 를 먼저 실행하세요.")
    else:
        scope = extraction["scope_sentiment"] or "전체"
        add(f"*(extraction #{extraction['id']} · 대상 {extraction['n_reviews']}건 · "
            f"범위 {scope} · 모델 {extraction['model']})*")
        add("")
        if extraction["summary"]:
            add("**전체 요약**")
            add("")
            add(extraction["summary"])
            add("")
        if extraction["pos_keywords"]:
            add(f"**칭찬 키워드**: {extraction['pos_keywords']}")
            add("")
        if extraction["neg_keywords"]:
            add(f"**불만 키워드**: {extraction['neg_keywords']}")
            add("")
        if extraction["complaint_types"]:
            add("**주요 유형**")
            add("")
            add("```")
            add(extraction["complaint_types"])
            add("```")
            add("")
        if extraction["suggestions"]:
            add("**개선 제안**")
            add("")
            add(extraction["suggestions"])
            add("")

    # ------------------------------------------------------------ 차트 목록
    add("## 생성된 차트")
    add("")
    if charts:
        for path in charts:
            try:
                relative = path.relative_to(config_module.PROJECT_ROOT)
            except ValueError:
                relative = path
            add(f"- `{relative}`")
    else:
        add("- 생성된 차트가 없습니다.")
    add("")

    return "\n".join(lines)


def markdown_to_text(markdown: str) -> str:
    """MD 문법을 걷어내 TXT 저장용 평문으로 바꾼다."""
    lines: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:]
            lines.extend(["=" * 60, title.center(60), "=" * 60])
        elif stripped.startswith("## "):
            lines.extend(["", f"[{stripped[3:]}]"])
        elif stripped.startswith("|"):
            # 표 구분선(|---|---|)은 평문에서 의미가 없어 버린다.
            if set(stripped) <= set("|- :"):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            lines.append("  " + "  ".join(cells))
        elif stripped in ("```",):
            continue
        else:
            lines.append(line.replace("**", ""))
    lines.extend(["", "=" * 60])
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI

def cmd_dashboard(args, cfg: dict[str, Any]) -> int:
    filters: dict[str, Any] = {}
    for attr in ("date_from", "date_to", "product"):
        value = getattr(args, attr, None)
        if value:
            filters[attr] = value

    db_path = config_module.resolve_path(cfg["paths"]["db"])
    charts_dir = config_module.resolve_path(cfg["paths"]["charts"])
    reports_dir = config_module.resolve_path(cfg["paths"]["reports"])

    with Database(db_path) as db:
        if db.counts_summary()["clean"] == 0:
            logger.error("정제된 리뷰가 없습니다. import → clean 을 먼저 실행하세요.")
            return 1

        charts: list[Path] = []
        if not args.no_charts:
            logger.info("차트 생성 중...")
            charts = visualize.generate_all(
                db, cfg, charts_dir, trend_unit=getattr(args, "trend_unit", None), **filters
            )

        markdown = build_report(db, cfg, charts, top_n=args.top_n, **filters)

        html_path = None
        if args.html:
            from src.html_dashboard import build_html_dashboard
            html_path = build_html_dashboard(db, cfg, charts, markdown, reports_dir, **filters)

    # 콘솔 출력은 평문이 읽기 좋다.
    print()
    print(markdown_to_text(markdown))
    print()

    reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = "md" if args.format == "md" else "txt"
    report_path = reports_dir / f"report_{visualize.timestamp()}.{suffix}"
    content = markdown if suffix == "md" else markdown_to_text(markdown)
    report_path.write_text(content, encoding="utf-8")

    logger.info("리포트 저장: %s", report_path)
    if html_path:
        logger.info("HTML 대시보드 저장: %s", html_path)
    return 0
