"""로깅 설정. 콘솔과 파일에 INFO/WARNING/ERROR 를 함께 기록한다."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONSOLE_FORMAT = "[%(levelname)s] %(message)s"
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def setup_logging(log_path: str | Path | None = None, verbose: bool = False) -> logging.Logger:
    """루트 로거를 구성하고 반환한다.

    콘솔은 과제 예시에 맞춰 `[INFO] 메시지` 형태로 간결하게,
    파일에는 시각·모듈명까지 남겨 사후 추적이 가능하게 한다.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    # 중복 실행 시 핸들러가 쌓이지 않도록 초기화한다.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    root.addHandler(console)

    if log_path:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        root.addHandler(file_handler)

    # 외부 라이브러리의 수다스러운 로그는 낮춘다.
    for noisy in ("openai", "httpx", "httpcore", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return root
