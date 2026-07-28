"""pytest 루트 설정.

이 파일이 루트에 있어야 pytest 가 저장소 루트를 sys.path 에 넣어주고,
테스트에서 `from src.cleaner import ...` 처럼 임포트할 수 있다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
