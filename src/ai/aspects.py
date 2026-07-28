"""속성별 감정 분석(ABSA).

리뷰 하나에 라벨 하나를 붙이면 `"용량이 작아서 1인분만 가능합니다. 혼자 살면 충분해요"`
같은 리뷰가 통째로 뭉개진다. 실제로는 '용량은 불만, 전반은 수용'인데 결과는 중립 하나다.

그래서 배송·품질·가격 같은 **속성별로 감정을 쪼개** 저장한다.
이렇게 하면 "무엇부터 고쳐야 하는가"가 언급량 × 순감정으로 바로 나온다.
"""

from __future__ import annotations

import logging
from typing import Any

from src import config as config_module
from src.ai.client import AIClient, AIError
from src.ai.sentiment import normalize_label, normalize_score
from src.db import Database

logger = logging.getLogger(__name__)

DEFAULT_ASPECTS = ["배송", "품질", "가격", "고객서비스", "사용성", "디자인"]

SYSTEM_PROMPT = (
    "당신은 고객 리뷰에서 속성별 감정을 추출하는 분석가입니다. "
    "한국어와 영어 리뷰를 모두 처리하며, 결과의 속성명은 주어진 목록의 한국어 표기를 그대로 씁니다.\n"
    "규칙:\n"
    "1. 리뷰에서 **실제로 언급된 속성만** 추출하세요. 언급되지 않은 속성은 넣지 마세요.\n"
    "2. 각 속성마다 positive / negative / neutral 중 하나를 고르세요.\n"
    "3. evidence 에는 그 판단의 근거가 된 **원문 구절을 그대로** 짧게 인용하세요.\n"
    "4. 한 리뷰 안에서 속성마다 감정이 달라도 됩니다(예: 배송은 부정, 품질은 긍정).\n"
    "반드시 다음 JSON 형식으로만 답하세요:\n"
    '{"aspects": [{"aspect": "배송", "sentiment": "negative", '
    '"score": 0.9, "evidence": "배송이 일주일 넘게 걸렸습니다"}]}'
)

# mock 모드용 속성 키워드. API 없이 파이프라인을 검증하기 위한 최소 사전이다.
ASPECT_KEYWORDS: dict[str, list[str]] = {
    "배송": ["배송", "도착", "택배", "발송", "오배송", "지연", "shipping", "delivery", "arrived"],
    "품질": ["품질", "불량", "마감", "내구", "튼튼", "고장", "흠집", "스크래치",
             "quality", "defective", "sturdy"],
    "가격": ["가격", "가성비", "비싸", "저렴", "값", "price", "cheap", "expensive", "worth"],
    "고객서비스": ["고객센터", "상담", "응대", "문의", "교환", "환불", "as", "a/s",
                   "service", "support", "reply"],
    "사용성": ["사용", "조작", "편하", "편리", "간편", "설정", "연결", "조립", "세척",
               "easy", "assembly", "clean"],
    "디자인": ["디자인", "색상", "외관", "예쁘", "깔끔", "크기", "무게", "포장",
               "design", "color", "compact"],
}

_POSITIVE_HINTS = ["좋", "만족", "빠르", "빨랐", "훌륭", "편하", "편리", "깔끔", "튼튼",
                   "정확", "추천", "amazing", "excellent", "great", "easy", "perfect"]
_NEGATIVE_HINTS = ["불량", "지연", "늦", "최악", "실망", "아쉽", "불친절", "고장", "엉망",
                   "샙니다", "방전", "끊", "안 됩", "frustrating", "never", "defective"]


def resolve_aspects(cfg: dict[str, Any]) -> list[str]:
    """설정에서 속성 목록을 읽는다. 도메인이 바뀌면 config.json 만 고치면 된다."""
    configured = cfg.get("absa", {}).get("aspects")
    return list(configured) if configured else list(DEFAULT_ASPECTS)


def mock_aspects(text: str, rating: int | None, aspect_list: list[str]) -> list[dict[str, Any]]:
    """API 없이 키워드로 속성을 잡아낸다(파이프라인 검증용).

    실제 분석을 대체하려는 게 아니라, 키 없이 전체 흐름을 돌려보기 위한 최소 구현이다.
    """
    lowered = text.lower()
    results: list[dict[str, Any]] = []

    for aspect in aspect_list:
        keywords = ASPECT_KEYWORDS.get(aspect, [])
        hit = next((k for k in keywords if k in lowered), None)
        if not hit:
            continue

        positive = sum(1 for w in _POSITIVE_HINTS if w in lowered)
        negative = sum(1 for w in _NEGATIVE_HINTS if w in lowered)

        if negative > positive:
            sentiment = "negative"
        elif positive > negative:
            sentiment = "positive"
        elif rating is not None:
            sentiment = "positive" if rating >= 4 else "negative" if rating <= 2 else "neutral"
        else:
            sentiment = "neutral"

        results.append({
            "aspect": aspect, "sentiment": sentiment, "score": 0.6,
            "evidence": f"[mock] '{hit}' 언급",
        })

    return results


def parse_aspects(data: dict[str, Any], aspect_list: list[str]) -> list[dict[str, Any]]:
    """모델 응답을 저장 가능한 형태로 정리한다.

    목록에 없는 속성을 지어내는 경우가 있어 화이트리스트로 거른다.
    같은 속성이 중복으로 오면 첫 번째만 남긴다(테이블이 UNIQUE 제약을 갖기 때문).
    """
    allowed = {a: a for a in aspect_list}
    # 영문으로 답하는 경우를 대비한 최소 대응표
    aliases = {
        "delivery": "배송", "shipping": "배송", "quality": "품질", "price": "가격",
        "cost": "가격", "service": "고객서비스", "customer service": "고객서비스",
        "usability": "사용성", "ease of use": "사용성", "design": "디자인",
    }

    raw_items = data.get("aspects")
    if not isinstance(raw_items, list):
        return []

    seen: set[str] = set()
    results: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        name = str(item.get("aspect", "")).strip()
        canonical = allowed.get(name) or aliases.get(name.lower())
        if not canonical or canonical in seen:
            continue

        sentiment = normalize_label(item.get("sentiment"))
        if sentiment is None:
            continue

        evidence = str(item.get("evidence") or "").strip() or None
        seen.add(canonical)
        results.append({
            "aspect": canonical,
            "sentiment": sentiment,
            "score": normalize_score(item.get("score")),
            "evidence": evidence[:200] if evidence else None,
        })

    return results


def analyze_one(
    client: AIClient, text: str, rating: int | None, lang: str | None,
    aspect_list: list[str],
) -> list[dict[str, Any]]:
    """리뷰 1건의 속성별 감정을 뽑는다."""
    if client.mock:
        return mock_aspects(text, rating, aspect_list)

    lang_hint = "이 리뷰는 영어로 작성되었습니다. " if lang == "en" else ""
    user_prompt = (
        f"{lang_hint}가능한 속성 목록: {', '.join(aspect_list)}\n\n"
        f"리뷰: {text}\n\n"
        "이 리뷰에서 실제로 언급된 속성만 골라 감정을 분석하세요."
    )

    data = client.complete_json(SYSTEM_PROMPT, user_prompt)
    return parse_aspects(data, aspect_list)


def analyze_aspects(
    db: Database, cfg: dict[str, Any], mock: bool = False,
    mode: str = "unanalyzed", review_id: int | None = None,
    limit: int | None = None, **filters,
) -> dict[str, int]:
    """대상 리뷰의 속성별 감정을 분석해 저장한다."""
    client = AIClient(cfg, mock=mock)
    aspect_list = resolve_aspects(cfg)
    targets = db.fetch_for_aspect_analysis(
        mode=mode, review_id=review_id, limit=limit, **filters
    )

    if not targets:
        logger.info("속성 분석할 리뷰가 없습니다. (이미 모두 분석되었거나 조건에 맞는 리뷰가 없음)")
        return {"target": 0, "success": 0, "failed": 0, "no_aspect": 0, "rows": 0}

    logger.info("속성 분석 대상: %d건 (속성 %s / 모델=%s)",
                len(targets), "·".join(aspect_list), client.model_name)

    success = failed = no_aspect = rows = 0
    total = len(targets)

    for index, row in enumerate(targets, start=1):
        try:
            items = analyze_one(client, row["review_text"], row["rating"],
                                row["lang"], aspect_list)
        except AIError as exc:
            failed += 1
            logger.error("[%d/%d] ID=%s 속성 분석 실패 — 건너뜁니다: %s",
                         index, total, row["id"], exc)
            continue

        if not items:
            # 어떤 속성도 언급되지 않은 리뷰("좋아요" 같은)는 정상적인 결과다.
            no_aspect += 1
            logger.debug("[%d/%d] ID=%s 언급된 속성 없음", index, total, row["id"])
            continue

        rows += db.save_aspects(row["id"], items, client.model_name)
        success += 1
        summary = ", ".join(f"{i['aspect']}:{i['sentiment'][:3]}" for i in items)
        logger.info("[%d/%d] ID=%s → %s", index, total, row["id"], summary)

    return {"target": total, "success": success, "failed": failed,
            "no_aspect": no_aspect, "rows": rows}


# ------------------------------------------------------------------ 집계 출력

def build_lines(stats: list[dict[str, Any]], min_mentions: int = 1) -> list[str]:
    """속성별 표와 개선 우선순위. 콘솔·리포트가 공유한다."""
    from src.viewer import display_width, pad

    if not stats:
        return ["- 속성 분석 결과가 없습니다. `python main.py aspects --unanalyzed` 를 먼저 실행하세요."]

    lines: list[str] = []
    name_width = max(max(display_width(s["aspect"]) for s in stats), display_width("속성"))

    header = (f"{pad('속성', name_width)} | 언급 | 긍정 | 중립 | 부정 | 부정률 | 순감정")
    lines.append(header)
    lines.append("-" * (display_width(header) + 2))

    for item in stats:
        # 순감정은 부호를 명시해야 좋고 나쁨이 한눈에 들어온다.
        lines.append(
            f"{pad(item['aspect'], name_width)} | "
            f"{item['mentions']:>4} | {item['positive']:>4} | {item['neutral']:>4} | "
            f"{item['negative']:>4} | {item['negative_rate']:>5.1f}% | {item['net']:+.2f}"
        )

    # 개선 우선순위 = 영향 범위(부정 건수) × 심각도(부정률).
    #
    # 부정 '건수'만으로 줄을 세우면, 언급이 많아서 부정도 덩달아 많은 속성이 위로 온다.
    # 반대로 '부정률'만 보면 2건 중 2건 부정인 속성이 1위가 되어 과대평가된다.
    # 둘을 곱해야 "많은 사람이, 자주 불만을 겪는" 속성이 실제로 위로 올라온다.
    candidates = [s for s in stats if s["mentions"] >= min_mentions and s["negative"]]
    if candidates:
        ranked = sorted(candidates, key=lambda s: -(s["negative"] * s["negative_rate"]))
        lines.append("")
        lines.append("[개선 우선순위]")
        for index, item in enumerate(ranked[:3], start=1):
            lines.append(
                f"{index}. {item['aspect']} — 부정 {item['negative']}건 / "
                f"언급 {item['mentions']}건 "
                f"(부정률 {item['negative_rate']:.1f}%, 순감정 {item['net']:+.2f})"
            )

    best = max(stats, key=lambda s: s["net"])
    worst = min(stats, key=lambda s: s["net"])
    if best["aspect"] != worst["aspect"]:
        lines.append("")
        lines.append("[해석]")
        lines.append(
            f"'{best['aspect']}'이(가) 순감정 {best['net']:+.2f}로 가장 좋고, "
            f"'{worst['aspect']}'이(가) {worst['net']:+.2f}로 가장 나쁩니다. "
            "리뷰 전체 감정 하나만 보면 이 차이가 평균에 묻힙니다."
        )

    return lines


# --------------------------------------------------------------------- CLI

def cmd_aspects(args, cfg: dict[str, Any]) -> int:
    filters: dict[str, Any] = {}
    for attr in ("product", "date_from", "date_to"):
        value = getattr(args, attr, None)
        if value:
            filters[attr] = value

    db_path = config_module.resolve_path(cfg["paths"]["db"])
    min_mentions = cfg.get("absa", {}).get("min_mentions", 1)

    if args.id is not None:
        mode = "id"
    elif args.all:
        mode = "all"
    elif args.unanalyzed:
        mode = "unanalyzed"
    else:
        mode = None  # 분석 없이 집계만 보여준다

    try:
        with Database(db_path) as db:
            if mode is not None:
                stats_run = analyze_aspects(
                    db, cfg, mock=args.mock, mode=mode,
                    review_id=args.id, limit=args.limit, **filters,
                )
                if stats_run["target"]:
                    logger.info(
                        "속성 분석 완료: %d건 성공(속성 %d개 저장), %d건 실패, %d건 속성 없음",
                        stats_run["success"], stats_run["rows"],
                        stats_run["failed"], stats_run["no_aspect"],
                    )

            stats = db.aspect_stats(**filters)
            coverage = db.aspect_coverage()

    except AIError as exc:
        logger.error("%s", exc)
        return 2

    print()
    print("=== 속성별 감정 분석 ===")
    if filters:
        print(f"조건: {', '.join(f'{k}={v}' for k, v in filters.items())}")
    if coverage["reviews"]:
        print(f"분석된 리뷰 {coverage['reviews']}건에서 속성 {coverage['rows']}건 추출")
    for line in build_lines(stats, min_mentions=min_mentions):
        print(line)
    print()
    return 0
