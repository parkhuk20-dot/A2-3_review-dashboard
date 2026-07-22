"""[보너스] 단일 HTML 대시보드 생성.

차트 PNG 를 base64 로 인라인 임베드해, 파일 하나만 열면 되도록 만든다.
(외부 CSS/JS/이미지 의존이 없어 메일 첨부나 파일 공유로도 그대로 열린다.)
"""

from __future__ import annotations

import base64
import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src import alert as alert_module
from src.db import Database
from src.report import keyword_counter, quality_metrics
from src.viewer import SENTIMENT_KO, build_stats

logger = logging.getLogger(__name__)

STYLE = """
:root {
  --bg: #f5f7fa; --card: #ffffff; --text: #1f2933; --muted: #6b7785;
  --line: #e3e8ee; --pos: #2E86DE; --neu: #95A5A6; --neg: #E74C3C;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 20px; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
               "Malgun Gothic", "Noto Sans KR", sans-serif;
  line-height: 1.65;
}
.wrap { max-width: 1080px; margin: 0 auto; }
header { margin-bottom: 28px; }
h1 { font-size: 28px; margin: 0 0 8px; }
.meta { color: var(--muted); font-size: 14px; }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 22px 24px; margin-bottom: 22px;
}
.card h2 { font-size: 18px; margin: 0 0 16px; padding-bottom: 10px;
           border-bottom: 1px solid var(--line); }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; }
.kpi { background: var(--bg); border-radius: 10px; padding: 16px; text-align: center; }
.kpi .label { font-size: 13px; color: var(--muted); }
.kpi .value { font-size: 26px; font-weight: 700; margin-top: 4px; }
.bar { height: 10px; border-radius: 5px; background: var(--line); overflow: hidden;
       display: flex; margin: 6px 0 14px; }
.bar span { display: block; height: 100%; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; }
th { background: var(--bg); font-weight: 600; }
td.num, th.num { text-align: right; }
ol, ul { margin: 0; padding-left: 20px; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap: 18px; }
.charts figure { margin: 0; }
.charts img { width: 100%; border: 1px solid var(--line); border-radius: 10px; }
.charts figcaption { font-size: 13px; color: var(--muted); margin-top: 6px; text-align: center; }
.alert-box { border-radius: 10px; padding: 14px 16px; font-size: 14px; }
.alert-danger { background: #fdecea; border: 1px solid #f5c2bd; color: #a33025; }
.alert-ok { background: #eaf6ec; border: 1px solid #bfe3c6; color: #2c6b3a; }
.summary { background: var(--bg); border-radius: 10px; padding: 16px; white-space: pre-wrap; }
.tag { display: inline-block; background: var(--bg); border: 1px solid var(--line);
       border-radius: 999px; padding: 3px 11px; margin: 3px 4px 3px 0; font-size: 13px; }
footer { color: var(--muted); font-size: 13px; text-align: center; margin-top: 30px; }
@media (max-width: 640px) { .charts { grid-template-columns: 1fr; } }
"""

CHART_CAPTIONS = {
    "sentiment_distribution.png": "감정 분포",
    "sentiment_trend.png": "시간별 감정 추이",
    "rating_sentiment_matrix.png": "별점별 감정 분포",
    "product_comparison.png": "제품별 감정 비교",
}


def _embed_image(path: Path) -> str | None:
    """PNG 를 data URI 로 바꾼다. 외부 파일 의존을 없애기 위함."""
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        logger.warning("차트를 HTML 에 넣지 못했습니다 (%s): %s", path, exc)
        return None
    return f"data:image/png;base64,{encoded}"


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else "-"))


def build_html_dashboard(
    db: Database, cfg: dict[str, Any], charts: list[Path],
    markdown: str, out_dir: Path, **filters,
) -> Path:
    """차트와 지표를 담은 단일 HTML 파일을 만든다."""
    stats = build_stats(db, **filters)
    metrics = quality_metrics(db, stats, **filters)
    extraction = db.latest_extraction()
    spike = alert_module.detect_spike(db, cfg, **filters)
    top_n = cfg.get("report", {}).get("top_n", 5)

    analyzed = stats["analyzed"] or 1
    counts = stats["sentiment_counts"]
    positive_rate = counts.get("positive", 0) / analyzed * 100
    neutral_rate = counts.get("neutral", 0) / analyzed * 100
    negative_rate = counts.get("negative", 0) / analyzed * 100

    parts: list[str] = []
    add = parts.append

    add("<div class='wrap'>")
    add("<header>")
    add("<h1>고객 리뷰 감정 분석 대시보드</h1>")
    period = (f" · 분석 기간 {stats['date_from']} ~ {stats['date_to']}"
              if stats["date_from"] else "")
    add(f"<div class='meta'>생성일시 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{_esc(period)}</div>")
    add("</header>")

    # ------------------------------------------------------------ 핵심 지표
    add("<section class='card'><h2>핵심 지표</h2><div class='kpis'>")
    kpis = [
        ("총 리뷰 수", f"{stats['total']}건"),
        ("분석 완료율", f"{stats['analyzed_rate']:.1f}%"),
        ("긍정 비율", f"{positive_rate:.1f}%"),
        ("평균 별점", f"{stats['avg_rating']:.2f}" if stats["avg_rating"] is not None else "-"),
        ("평균 감정 점수", f"{stats['avg_score']:.2f}" if stats["avg_score"] is not None else "-"),
    ]
    for label, value in kpis:
        add(f"<div class='kpi'><div class='label'>{_esc(label)}</div>"
            f"<div class='value'>{_esc(value)}</div></div>")
    add("</div></section>")

    # ------------------------------------------------------------ 감정 분포
    add("<section class='card'><h2>감정 분포</h2>")
    add("<div class='bar'>"
        f"<span style='width:{positive_rate:.2f}%;background:var(--pos)'></span>"
        f"<span style='width:{neutral_rate:.2f}%;background:var(--neu)'></span>"
        f"<span style='width:{negative_rate:.2f}%;background:var(--neg)'></span>"
        "</div>")
    add("<table><tr><th>감정</th><th class='num'>건수</th><th class='num'>비율</th></tr>")
    for label in ("positive", "neutral", "negative"):
        count = counts.get(label, 0)
        add(f"<tr><td>{SENTIMENT_KO[label]}</td><td class='num'>{count}건</td>"
            f"<td class='num'>{count / analyzed * 100:.1f}%</td></tr>")
    add("</table></section>")

    # ------------------------------------------------------- 감정 변화 알림
    add("<section class='card'><h2>감정 변화 알림 <span class='meta'>(보너스)</span></h2>")
    css_class = "alert-danger" if (spike and spike["triggered"]) else "alert-ok"
    add(f"<div class='alert-box {css_class}'>")
    for line in alert_module.format_alert(spike):
        add(f"<div>{_esc(line.lstrip('- ').replace('**', ''))}</div>")
    add("</div></section>")

    # ------------------------------------------------------------ 품질 지표
    add("<section class='card'><h2>품질 지표</h2><table>")
    add("<tr><th>지표</th><th class='num'>값</th></tr>")
    quality_rows = [
        ("정제 통과율",
         f"{metrics['clean_rate']:.1f}% (원본 {stats['raw_total']} → 정제 {stats['clean_total']})"),
        ("감정 분석 완료율", f"{metrics['analyzed_rate']:.1f}%"),
        ("별점–감정 일치율",
         f"{metrics['agreement_rate']:.1f}% (비교 {metrics['agreement_base']}건)"),
        ("평균 신뢰도 점수",
         f"{metrics['avg_score']:.2f}" if metrics["avg_score"] is not None else "-"),
        ("결측률",
         f"별점 없음 {metrics['missing_rating_rate']:.1f}% · 작성일 없음 {metrics['missing_date']}건"),
    ]
    for label, value in quality_rows:
        add(f"<tr><td>{_esc(label)}</td><td class='num'>{_esc(value)}</td></tr>")
    add("</table></section>")

    # --------------------------------------------------------------- TOP N
    add(f"<section class='card'><h2>TOP {top_n} 키워드</h2>")
    add("<div class='kpis' style='grid-template-columns:repeat(auto-fit,minmax(260px,1fr))'>")
    for title, keywords in (
        ("긍정 키워드", keyword_counter(db, "positive", top_n, **filters)),
        ("부정 키워드", keyword_counter(db, "negative", top_n, **filters)),
    ):
        add(f"<div style='text-align:left'><strong>{_esc(title)}</strong><ol>")
        if keywords:
            for keyword, count in keywords:
                add(f"<li>{_esc(keyword)} <span class='meta'>({count}회)</span></li>")
        else:
            add("<li class='meta'>집계된 키워드가 없습니다.</li>")
        add("</ol></div>")
    add("</div></section>")

    # ---------------------------------------------------------- 제품별 비교
    product_rows = db.product_stats(by="product", **filters)
    if product_rows:
        add("<section class='card'><h2>제품별 비교 <span class='meta'>(보너스)</span></h2><table>")
        add("<tr><th>제품</th><th class='num'>리뷰 수</th><th class='num'>평균 별점</th>"
            "<th class='num'>긍정</th><th class='num'>중립</th><th class='num'>부정</th>"
            "<th class='num'>부정 비율</th></tr>")
        for row in product_rows:
            analyzed_here = (row["positive"] or 0) + (row["neutral"] or 0) + (row["negative"] or 0)
            neg_rate = (row["negative"] or 0) / analyzed_here * 100 if analyzed_here else 0.0
            avg = f"{row['avg_rating']:.2f}" if row["avg_rating"] is not None else "-"
            add(f"<tr><td>{_esc(row['name'])}</td><td class='num'>{row['n_reviews']}건</td>"
                f"<td class='num'>{avg}</td><td class='num'>{row['positive'] or 0}</td>"
                f"<td class='num'>{row['neutral'] or 0}</td><td class='num'>{row['negative'] or 0}</td>"
                f"<td class='num'>{neg_rate:.1f}%</td></tr>")
        add("</table></section>")

    # -------------------------------------------------------- AI 인사이트
    add("<section class='card'><h2>AI 인사이트</h2>")
    if extraction is None:
        add("<p class='meta'>추출 결과가 없습니다. <code>python main.py extract</code> 를 먼저 실행하세요.</p>")
    else:
        scope = extraction["scope_sentiment"] or "전체"
        add(f"<p class='meta'>extraction #{extraction['id']} · 대상 {extraction['n_reviews']}건 · "
            f"범위 {_esc(scope)} · 모델 {_esc(extraction['model'])}</p>")
        if extraction["summary"]:
            add(f"<div class='summary'>{_esc(extraction['summary'])}</div>")
        for title, value in (("칭찬 키워드", extraction["pos_keywords"]),
                             ("불만 키워드", extraction["neg_keywords"])):
            if value:
                add(f"<p><strong>{_esc(title)}</strong><br>")
                for keyword in str(value).split(","):
                    if keyword.strip():
                        add(f"<span class='tag'>{_esc(keyword.strip())}</span>")
                add("</p>")
        if extraction["complaint_types"]:
            add("<p><strong>주요 유형</strong></p>"
                f"<div class='summary'>{_esc(extraction['complaint_types'])}</div>")
        if extraction["suggestions"]:
            add("<p><strong>개선 제안</strong></p><ul>")
            for line in str(extraction["suggestions"]).splitlines():
                cleaned = line.lstrip("- ").strip()
                if cleaned:
                    add(f"<li>{_esc(cleaned)}</li>")
            add("</ul>")
    add("</section>")

    # ------------------------------------------------------------ 차트 임베드
    add("<section class='card'><h2>차트</h2><div class='charts'>")
    embedded = 0
    for path in charts:
        data_uri = _embed_image(path)
        if not data_uri:
            continue
        caption = CHART_CAPTIONS.get(path.name, path.stem)
        add(f"<figure><img src='{data_uri}' alt='{_esc(caption)}'>"
            f"<figcaption>{_esc(caption)}</figcaption></figure>")
        embedded += 1
    if embedded == 0:
        add("<p class='meta'>생성된 차트가 없습니다. <code>--no-charts</code> 없이 실행해 보세요.</p>")
    add("</div></section>")

    add("<footer>AI 기반 고객 리뷰 감정 분석 대시보드 · CLI 생성 리포트</footer>")
    add("</div>")

    document = (
        "<!DOCTYPE html>\n<html lang='ko'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        "<title>고객 리뷰 감정 분석 대시보드</title>\n"
        f"<style>{STYLE}</style>\n</head>\n<body>\n"
        + "\n".join(parts)
        + "\n</body>\n</html>\n"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    path.write_text(document, encoding="utf-8")
    logger.info("HTML 대시보드 생성: 차트 %d개 임베드", embedded)
    return path
