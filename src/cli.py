"""argparse 서브커맨드 정의와 라우팅.

공통 옵션(--config/--verbose/--mock)은 서브커맨드 앞뒤 어디에 써도 동작하도록
argparse 에 넘기기 전에 먼저 걷어낸다. (parents= 로 공유하면 서브파서 기본값이
앞쪽 값을 덮어써 조용히 무시되는 문제가 있어 선파싱 방식을 쓴다.)
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from typing import Any

from src import config as config_module
from src.logger import setup_logging

logger = logging.getLogger(__name__)

# 서브커맨드 → (모듈, 함수) 매핑. 지연 임포트로 필요한 것만 로드한다.
HANDLERS: dict[str, tuple[str, str]] = {
    "import": ("src.importer", "cmd_import"),
    "add": ("src.importer", "cmd_add"),
    "clean": ("src.cleaner", "cmd_clean"),
    "analyze": ("src.ai.sentiment", "cmd_analyze"),
    "extract": ("src.ai.extractor", "cmd_extract"),
    "list": ("src.viewer", "cmd_list"),
    "show": ("src.viewer", "cmd_show"),
    "stats": ("src.viewer", "cmd_stats"),
    "dashboard": ("src.report", "cmd_dashboard"),
    "export": ("src.exporter", "cmd_export"),
    "alert": ("src.alert", "cmd_alert"),
    "compare": ("src.compare", "cmd_compare"),
}


def pre_parse_globals(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """공통 옵션을 위치에 상관없이 먼저 뽑아낸다."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=None)
    pre.add_argument("--verbose", "-v", action="store_true")
    pre.add_argument("--mock", action="store_true")
    return pre.parse_known_args(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="AI 기반 고객 리뷰 감정 분석 대시보드",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python main.py import --file data/sample_reviews.csv\n"
            "  python main.py clean --dedup skip\n"
            "  python main.py analyze --unanalyzed --limit 20\n"
            "  python main.py extract --sentiment negative\n"
            "  python main.py list --sentiment negative --page 1 --size 5\n"
            "  python main.py stats\n"
            "  python main.py dashboard --format md --html\n"
            "  python main.py export --format xlsx --rating-min 4\n"
        ),
    )
    # 도움말에 보이도록 공통 옵션도 등록해 둔다(값은 선파싱 결과를 쓴다).
    parser.add_argument("--config", default=None, help="설정 파일 경로 (기본: config.json)")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG 로그까지 출력")
    parser.add_argument("--mock", action="store_true", help="AI API 호출 없이 모의 결과 사용")

    sub = parser.add_subparsers(dest="command", metavar="<서브커맨드>")

    # ------------------------------------------------------------ import/add
    p_import = sub.add_parser("import", help="CSV/Excel 파일에서 리뷰 수집 → raw 저장소")
    p_import.add_argument("--file", required=True, help="읽어올 CSV 또는 Excel 파일 경로")
    p_import.add_argument("--sheet", default=None, help="Excel 시트 이름 (기본: 첫 시트)")
    p_import.add_argument("--encoding", default=None, help="CSV 인코딩 (기본: utf-8-sig)")
    p_import.add_argument("--limit", type=int, default=None, help="읽을 최대 행 수")
    p_import.add_argument("--product", default=None, help="파일에 제품 컬럼이 없을 때 일괄 지정")
    p_import.add_argument("--category", default=None, help="파일에 카테고리 컬럼이 없을 때 일괄 지정")

    p_add = sub.add_parser("add", help="리뷰 1건을 직접 입력해 raw 저장소에 추가")
    p_add.add_argument("--text", required=True, help="리뷰 본문")
    p_add.add_argument("--rating", default=None, help="별점 1~5")
    p_add.add_argument("--date", dest="review_date", default=None, help="작성일 (예: 2026-07-01)")
    p_add.add_argument("--product", default=None, help="제품명")
    p_add.add_argument("--category", default=None, help="카테고리")

    # ----------------------------------------------------------------- clean
    p_clean = sub.add_parser("clean", help="원본 정제 · 중복 처리 → clean 저장소")
    p_clean.add_argument("--dedup", choices=["skip", "upsert"], default=None,
                         help="중복 처리 정책 (기본: config.json)")
    p_clean.add_argument("--min-length", type=int, default=None,
                         help="이보다 짧은 리뷰는 버린다 (기본: config.json)")
    p_clean.add_argument("--limit", type=int, default=None, help="처리할 최대 건수")
    p_clean.add_argument("--all", dest="reclean", action="store_true",
                         help="이미 정제한 원본도 다시 처리")

    # --------------------------------------------------------------- analyze
    p_analyze = sub.add_parser("analyze", help="AI 감정 분석 (긍정/부정/중립 + 신뢰도)")
    target = p_analyze.add_mutually_exclusive_group()
    target.add_argument("--all", action="store_true", help="전체 리뷰 재분석")
    target.add_argument("--unanalyzed", action="store_true",
                        help="아직 분석하지 않은 리뷰만 (기본값)")
    target.add_argument("--id", type=int, default=None, help="특정 리뷰 1건만")
    p_analyze.add_argument("--limit", type=int, default=None, help="분석할 최대 건수")
    p_analyze.add_argument("--product", default=None, help="특정 제품의 리뷰만")
    p_analyze.add_argument("--lang", default=None, help="특정 언어의 리뷰만 (ko/en)")

    # --------------------------------------------------------------- extract
    p_extract = sub.add_parser("extract", help="AI 키워드 · 요약 · 개선 제안 추출")
    p_extract.add_argument("--sentiment", choices=["positive", "negative", "neutral"],
                           default=None, help="특정 감정의 리뷰만 대상으로")
    p_extract.add_argument("--product", default=None, help="특정 제품의 리뷰만")
    p_extract.add_argument("--date-from", default=None, help="시작일 (YYYY-MM-DD)")
    p_extract.add_argument("--date-to", default=None, help="종료일 (YYYY-MM-DD)")
    p_extract.add_argument("--limit", type=int, default=None, help="AI 에 넘길 최대 리뷰 수")

    # ------------------------------------------------------------ list/show/stats
    p_list = sub.add_parser("list", help="리뷰 목록 조회 (필터 · 페이지네이션 · 정렬)")
    p_list.add_argument("--sentiment", choices=["positive", "negative", "neutral"], default=None)
    p_list.add_argument("--rating", type=int, default=None, help="정확히 이 별점만")
    p_list.add_argument("--rating-min", type=int, default=None)
    p_list.add_argument("--rating-max", type=int, default=None)
    p_list.add_argument("--date-from", default=None, help="시작일 (YYYY-MM-DD)")
    p_list.add_argument("--date-to", default=None, help="종료일 (YYYY-MM-DD)")
    p_list.add_argument("--product", default=None)
    p_list.add_argument("--category", default=None)
    p_list.add_argument("--lang", default=None, help="언어 (ko/en)")
    p_list.add_argument("--keyword", default=None, help="본문에 포함된 단어")
    p_list.add_argument("--page", type=int, default=1)
    p_list.add_argument("--size", type=int, default=10, help="페이지당 건수")
    p_list.add_argument("--sort", choices=["id", "date", "rating", "score", "length"],
                        default="date")
    p_list.add_argument("--order", choices=["asc", "desc"], default="desc")
    p_list.add_argument("--full", action="store_true", help="본문을 자르지 않고 출력")

    p_show = sub.add_parser("show", help="리뷰 상세 조회 (원문 + 분석 결과)")
    p_show.add_argument("--id", type=int, required=True)

    p_stats = sub.add_parser("stats", help="전체 통계 요약")
    p_stats.add_argument("--product", default=None)
    p_stats.add_argument("--category", default=None)
    p_stats.add_argument("--date-from", default=None)
    p_stats.add_argument("--date-to", default=None)

    # ------------------------------------------------------------- dashboard
    p_dash = sub.add_parser("dashboard", help="차트 생성 + 종합 리포트")
    p_dash.add_argument("--format", choices=["md", "txt"], default="md",
                        help="리포트 저장 형식")
    p_dash.add_argument("--top-n", type=int, default=None, help="TOP N 개수")
    p_dash.add_argument("--no-charts", action="store_true", help="차트 생성 생략")
    p_dash.add_argument("--html", action="store_true",
                        help="[보너스] 차트를 내장한 단일 HTML 대시보드도 생성")
    p_dash.add_argument("--date-from", default=None)
    p_dash.add_argument("--date-to", default=None)
    p_dash.add_argument("--product", default=None)

    # ---------------------------------------------------------------- export
    p_export = sub.add_parser("export", help="분석 결과 내보내기")
    p_export.add_argument("--format", choices=["csv", "jsonl", "xlsx"], default="csv")
    p_export.add_argument("--sentiment", choices=["positive", "negative", "neutral"], default=None)
    p_export.add_argument("--rating-min", type=int, default=None)
    p_export.add_argument("--rating-max", type=int, default=None)
    p_export.add_argument("--date-from", default=None)
    p_export.add_argument("--date-to", default=None)
    p_export.add_argument("--product", default=None)
    p_export.add_argument("--output", default=None, help="저장 경로 (기본: output/exports/…)")

    # ------------------------------------------------------------- 보너스
    p_alert = sub.add_parser("alert", help="[보너스] 최근 부정 리뷰 급증 경고")
    p_alert.add_argument("--days", type=int, default=None, help="최근 N일 (기본: config.json)")
    p_alert.add_argument("--threshold", type=float, default=None,
                         help="직전 기간 대비 몇 배 이상이면 경고 (기본: config.json)")
    p_alert.add_argument("--product", default=None)

    p_compare = sub.add_parser("compare", help="[보너스] 제품 · 카테고리별 비교 분석")
    p_compare.add_argument("--by", choices=["product", "category"], default="product")
    p_compare.add_argument("--products", default=None, help="쉼표로 구분한 비교 대상")
    p_compare.add_argument("--chart", action="store_true", help="비교 차트도 생성")
    p_compare.add_argument("--date-from", default=None)
    p_compare.add_argument("--date-to", default=None)

    return parser


def dispatch(command: str, args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """서브커맨드를 담당 모듈로 넘긴다."""
    module_name, func_name = HANDLERS[command]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        # 아직 구현되지 않은 단계일 때 원인을 분명히 알려준다.
        if exc.name and exc.name.startswith("src."):
            logger.error("'%s' 명령은 아직 구현되지 않았습니다 (%s).", command, module_name)
            return 2
        raise
    return int(getattr(module, func_name)(args, cfg) or 0)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    globals_ns, rest = pre_parse_globals(argv)
    parser = build_parser()
    args = parser.parse_args(rest)

    # 선파싱한 공통 옵션을 최종 결과로 확정한다.
    args.config = globals_ns.config
    args.verbose = globals_ns.verbose
    args.mock = globals_ns.mock

    if not args.command:
        parser.print_help()
        return 0

    cfg = config_module.load_config(args.config)
    config_module.ensure_dirs(cfg)
    setup_logging(config_module.resolve_path(cfg["paths"]["log"]), verbose=args.verbose)

    try:
        return dispatch(args.command, args, cfg)
    except KeyboardInterrupt:
        logger.warning("사용자가 중단했습니다.")
        return 130
    except Exception as exc:  # 예상 못 한 오류도 스택트레이스는 로그로만 남긴다.
        logger.error("실행 중 오류가 발생했습니다: %s", exc)
        logger.debug("상세 오류", exc_info=True)
        return 1
