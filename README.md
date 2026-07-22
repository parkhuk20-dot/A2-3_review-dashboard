# AI 기반 고객 리뷰 감정 분석 대시보드

고객 리뷰를 파일에서 수집해 정제하고, AI로 감정을 분석한 뒤,
키워드·요약·개선 제안을 추출해 **대시보드 리포트와 차트로 시각화**하는 CLI 애플리케이션입니다.

단순 감정 분류에서 끝나지 않고 **시간에 따른 감정 변화 추이 · 불만/칭찬 키워드 ·
별점과 감정의 상관관계**까지 뽑아, 비즈니스 의사결정에 바로 쓸 수 있는 형태로 만듭니다.

> 2026 Codyssey A2-3 과제 · Python 3.10+ · OpenAI API

---

## 목차

- [빠른 시작](#빠른-시작)
- [전체 파이프라인](#전체-파이프라인)
- [서브커맨드 레퍼런스](#서브커맨드-레퍼런스)
- [설정 (config.json)](#설정-configjson)
- [프로젝트 구조](#프로젝트-구조)
- [데이터 저장 구조](#데이터-저장-구조)
- [설계 근거](#설계-근거)
- [보너스 과제](#보너스-과제)
- [문제 해결](#문제-해결)

---

## 빠른 시작

### 1. 설치

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> 시스템 `python3` 가 3.9 이하라면 반드시 3.10 이상으로 가상환경을 만드세요.
> 이 프로젝트는 `str | None` 같은 3.10+ 타입 문법을 사용합니다.

### 2. API 키 설정

API 키는 **코드에도, `config.json` 에도 넣지 않습니다.** `.env` 파일로만 전달합니다.

```bash
cp .env.example .env
# .env 를 열어 OPENAI_API_KEY=sk-... 를 채워 넣으세요
```

`.env` 는 `.gitignore` 에 있어 커밋되지 않습니다.
셸에서 `export OPENAI_API_KEY=...` 를 해도 되지만, 그 값은 **그 셸에서만** 유효해
다른 터미널이나 스케줄러에서는 보이지 않습니다. 파일로 두는 쪽이 안전합니다.

### 3. 전체 흐름 한 번 돌려보기

```bash
.venv/bin/python main.py import --file data/sample_reviews.csv
.venv/bin/python main.py clean
.venv/bin/python main.py analyze --unanalyzed
.venv/bin/python main.py extract --sentiment negative
.venv/bin/python main.py dashboard --html
```

**API 키 없이 시험만 해보려면** 모든 AI 명령에 `--mock` 을 붙이세요.
규칙 기반 결과로 전체 파이프라인이 그대로 동작합니다.

```bash
.venv/bin/python main.py analyze --unanalyzed --mock
```

---

## 전체 파이프라인

```
 CSV / Excel 파일
        │
   [import] ─────────────▶  raw_reviews      원본 그대로 보존
        │                                    (원본 행 전체를 raw_json 에)
   [clean]  ─────────────▶  clean_reviews    검증·정규화·중복 처리 통과분
        │
   [analyze] ────────────▶  sentiments       리뷰별 감정 + 신뢰도 + 키워드
        │
   [extract] ────────────▶  extractions      키워드·요약·불만유형·개선제안
        │
        ├── [list] [show] [stats]      조회
        ├── [dashboard]                차트 4종 + 종합 리포트 (+ --html)
        ├── [export]                   CSV / JSONL / Excel
        └── [alert] [compare]          보너스
```

각 단계는 **독립적으로 실행**됩니다. 이전 단계의 저장 결과를 입력으로 받으므로,
중간에 실패해도 그 지점부터 다시 시작할 수 있습니다.

---

## 서브커맨드 레퍼런스

### `import` — 파일에서 리뷰 수집

```bash
python main.py import --file data/sample_reviews.csv
python main.py import --file data/sample_reviews.xlsx --sheet reviews
python main.py import --file reviews_no_product.csv --product "무선 이어폰 X200"
```

| 옵션 | 설명 |
|------|------|
| `--file` | 읽어올 CSV / TSV / Excel 파일 (필수) |
| `--sheet` | Excel 시트 이름 (기본: 첫 시트) |
| `--encoding` | CSV 인코딩 (기본: `utf-8-sig`) |
| `--limit` | 읽을 최대 행 수 |
| `--product` / `--category` | 파일에 해당 컬럼이 없을 때 일괄 지정 |

**컬럼 이름은 자동 매핑됩니다.** 파일마다 헤더가 제각각이라
`리뷰내용` / `review_text` / `content` / `후기` 모두 리뷰 본문으로 인식합니다.
매핑 표는 `config.json` 의 `import.column_aliases` 에서 바꿀 수 있습니다.

### `add` — 리뷰 1건 직접 추가

```bash
python main.py add --text "배송이 빨라서 좋았습니다" --rating 5 \
                   --date 2026-07-16 --product "무선 이어폰 X200"
```

### `clean` — 정제 및 중복 처리

```bash
python main.py clean                          # config.json 의 기본 정책
python main.py clean --dedup upsert           # 중복을 갱신으로 처리
python main.py clean --min-length 20          # 20자 미만 리뷰 제외
python main.py clean --all --dedup upsert     # 이미 정제한 원본도 다시 처리
```

적용되는 정제 규칙:

1. **필수 필드 검증** — 리뷰 본문이 없으면 제외
2. **텍스트 정규화** — HTML 태그·제어문자·중복 공백 제거, NFKC 정규화
3. **별점 범위 검증** — 1~5 밖이면 리뷰는 살리고 별점만 비움
4. **날짜 형식 통일** — `2026/06/13`, `2026.06.13`, `20260613` 등 10종 → `YYYY-MM-DD`
5. **짧은 리뷰 필터링** — 기본 10자 미만 제외

중복은 **정규화한 본문 + 제품명 해시**로 판정합니다.
리뷰는 URL 같은 고유키가 없어 내용 기반으로 볼 수밖에 없고,
공백·문장부호 차이는 같은 리뷰로 보되 제품이 다르면 다른 리뷰로 봅니다.

### `analyze` — AI 감정 분석

```bash
python main.py analyze --unanalyzed            # 미분석분만 (기본)
python main.py analyze --unanalyzed --limit 20 # 20건만
python main.py analyze --id 42                 # 특정 리뷰 재분석
python main.py analyze --all                   # 전체 재분석
python main.py analyze --unanalyzed --mock     # API 없이
```

- 감정 `positive` / `negative` / `neutral` + **신뢰도 점수 0.0~1.0** + 리뷰별 키워드
- 이미 분석된 리뷰는 기본 스킵(재호출 비용 방지). `--all` / `--id` 는 명시적 재분석
- API 실패는 **로깅 후 스킵**하고 다음 리뷰로 넘어갑니다 (전체가 멈추지 않음)
- 일시적 오류는 지수 백오프로 3회까지 재시도

### `extract` — AI 키워드 · 요약 · 개선 제안

```bash
python main.py extract                                  # 전체 리뷰 종합
python main.py extract --sentiment negative             # 부정 리뷰만
python main.py extract --product "로봇청소기 CleanBot"
python main.py extract --date-from 2026-07-01 --date-to 2026-07-15
```

추출 항목 5종: **긍정 키워드 / 부정 키워드 / 전체 요약 / 불만 유형 분류 / 개선 제안**

조건에 맞는 리뷰를 모아 **한 번의 호출로** 분석합니다.
리뷰별로 부르면 비용이 선형으로 늘고, 무엇보다 "전체를 관통하는 불만 유형" 같은 건
개별 리뷰만 봐서는 나오지 않기 때문입니다.

### `list` / `show` / `stats` — 조회

```bash
python main.py list --sentiment negative --page 1 --size 5
python main.py list --rating-min 4 --sort rating --order desc
python main.py list --date-from 2026-07-01 --product "스마트워치 FitPro"
python main.py list --lang en                     # 영어 리뷰만
python main.py list --keyword 배송 --full         # 본문 전체 출력

python main.py show --id 83
python main.py stats
python main.py stats --product "무선 이어폰 X200"
```

필터: `--sentiment` `--rating` `--rating-min/max` `--date-from/to`
`--product` `--category` `--lang` `--keyword`
정렬: `--sort {id,date,rating,score,length}` `--order {asc,desc}`

### `dashboard` — 차트 + 종합 리포트

```bash
python main.py dashboard                     # 차트 + 콘솔 출력 + MD 저장
python main.py dashboard --format txt        # TXT 로 저장
python main.py dashboard --html              # + 단일 HTML 대시보드 (보너스)
python main.py dashboard --trend-unit day    # 추이를 일 단위로
python main.py dashboard --no-charts         # 차트 없이 리포트만
```

**생성 차트 4종** (`output/charts/`)

| 파일 | 내용 |
|------|------|
| `sentiment_distribution.png` | 감정 분포 (막대 + 비율 라벨) |
| `sentiment_trend.png` | 시간별 감정 추이 (일/주 단위 선그래프) |
| `rating_sentiment_matrix.png` | 별점별 감정 분포 (누적 막대) |
| `product_comparison.png` | 제품별 감정 비교 + 평균 별점 (보너스) |

**리포트 구성** (`output/reports/`)

- 핵심 지표 5종 (총 리뷰 수 · 분석 완료율 · 긍정 비율 · 평균 별점 · 평균 감정 점수)
- 감정 분포 / 별점 분포
- 감정 변화 알림 (보너스)
- **품질 지표 5종**: 정제 통과율 · 분석 완료율 · **별점–감정 일치율** · 평균 신뢰도 · 결측률
- **TOP N 3종**: 긍정 키워드 · 부정 키워드 · 제품별 평균 별점
- AI 인사이트 (요약 · 키워드 · 불만 유형 · 개선 제안)
- 생성된 차트 목록

### `export` — 내보내기

```bash
python main.py export --format csv
python main.py export --format jsonl --sentiment negative
python main.py export --format xlsx --rating-min 4
python main.py export --format csv --date-from 2026-07-01 --output ./my_reviews.csv
```

| 형식 | 특징 |
|------|------|
| `csv` | `utf-8-sig`(BOM) — Excel 에서 한글이 깨지지 않음 |
| `jsonl` | 한 줄에 리뷰 하나 — 스트리밍·재처리에 유리 |
| `xlsx` | 헤더 스타일·열 너비·틀 고정 적용 |

### `alert` — 감정 급증 경고 (보너스)

```bash
python main.py alert                              # config.json 기본 (7일, 1.5배)
python main.py alert --days 14 --threshold 2.0
python main.py alert --product "스마트워치 FitPro"
```

최근 N일의 부정 리뷰 **비율**을 직전 같은 길이의 기간과 비교합니다.
건수가 아니라 비율로 보는 이유는, 리뷰가 전체적으로 늘어난 것과
불만이 실제로 심해진 것을 구분하기 위해서입니다.

### `compare` — 제품/카테고리 비교 (보너스)

```bash
python main.py compare --by product --chart
python main.py compare --by category
python main.py compare --products "무선 이어폰 X200,스마트워치 FitPro"
```

---

## 설정 (`config.json`)

`config.example.json` 을 복사해 `config.json` 을 만들고 필요한 값을 조정합니다.
**API 키는 절대 이 파일에 넣지 않습니다.**

```jsonc
{
  "clean": {
    "dedup_policy": "skip",     // skip | upsert
    "min_length": 10,           // 이보다 짧은 리뷰는 제외
    "rating_min": 1, "rating_max": 5,
    "strip_html": true
  },
  "ai": {
    "model": "gpt-4o-mini",
    "temperature": 0.2,
    "max_reviews_per_extraction": 80,   // extract 1회에 넘길 최대 리뷰 수
    "review_chars_for_extraction": 300  // 리뷰당 잘라 보낼 글자 수
  },
  "report": { "top_n": 5, "trend_unit": "week" },   // trend_unit: day | week
  "alert":  { "days": 7, "threshold": 1.5, "min_reviews": 5 },
  "viz":    { "dpi": 130, "figsize": [9, 5.5], "palette": { ... } },
  "paths":  { "db": "data/reviews.db", "charts": "output/charts", ... }
}
```

다른 설정 파일을 쓰려면 `--config` 를 주면 됩니다.
이 옵션은 **서브커맨드 앞뒤 어디에 써도** 동작합니다.

```bash
python main.py --config config.local.json stats
python main.py stats --config config.local.json     # 둘 다 같게 동작
```

### 로깅

`logging` 으로 INFO / WARNING / ERROR 3레벨을 기록합니다.

- 콘솔: `[INFO] 메시지` 형태로 간결하게
- 파일(`logs/dashboard.log`): 시각·모듈명까지 포함해 사후 추적 가능
- `--verbose` 를 주면 DEBUG 까지 출력

---

## 프로젝트 구조

```
A2-3_review-dashboard/
├── main.py                   # 엔트리포인트
├── config.json               # 설정 (API 키 미포함)
├── config.example.json
├── .env.example              # API 키 템플릿
├── requirements.txt
├── data/
│   ├── sample_reviews.csv    # 샘플 리뷰 110건 (한/영, 이상치 포함)
│   ├── sample_reviews.xlsx   # 영문 헤더 Excel 샘플 40건
│   └── reviews.db            # SQLite (자동 생성, gitignore)
├── docs/PLAN.md              # 설계 문서 · 진행 기록
├── scripts/sync_obsidian.sh  # 설계 문서 → Obsidian 동기화
└── src/                      # 15개 모듈
    ├── cli.py                # 서브커맨드 정의 · 라우팅
    ├── config.py             # 설정 로드 + .env 병합
    ├── logger.py             # 로깅 설정
    ├── retry.py              # 지수 백오프 재시도
    ├── db.py                 # SQLite 스키마 · CRUD · 집계 쿼리
    ├── importer.py           # import / add
    ├── cleaner.py            # clean
    ├── ai/
    │   ├── client.py         # OpenAI 래퍼 (JSON 강제 · 재시도 · mock)
    │   ├── sentiment.py      # analyze
    │   └── extractor.py      # extract
    ├── viewer.py             # list / show / stats
    ├── visualize.py          # matplotlib 차트
    ├── report.py             # dashboard 리포트
    ├── exporter.py           # export
    ├── alert.py              # [보너스] 감정 급증 알림
    ├── compare.py            # [보너스] 제품 비교
    └── html_dashboard.py     # [보너스] 단일 HTML 대시보드
```

---

## 데이터 저장 구조

**SQLite** (`data/reviews.db`) 에 5개 테이블로 영구 저장합니다.
메모리 자료구조만 쓰지 않으므로 프로그램을 껐다 켜도 데이터가 유지됩니다.

| 테이블 | 역할 |
|--------|------|
| `raw_reviews` | 파일에서 읽은 **원본** (원본 행 전체를 `raw_json` 에 보존) |
| `clean_reviews` | 정제·검증을 통과한 데이터 (`review_hash` UNIQUE 로 중복 차단) |
| `sentiments` | 리뷰별 감정 · 신뢰도 · 키워드 · 사용 모델 |
| `extractions` | 조건별 AI 추출 결과 (키워드 · 요약 · 불만유형 · 개선제안) |
| `import_log` | 파일 단위 수집 이력 (총/유효/스킵) |

`raw` 와 `clean` 을 분리한 이유는, 정제 규칙을 바꿔 다시 돌리고 싶을 때
원본이 남아 있어야 하기 때문입니다. `clean --all` 로 언제든 재정제할 수 있습니다.

---

## 설계 근거

| 결정 | 이유 |
|------|------|
| **raw / clean 분리** | 정제 규칙을 바꿔 재실행할 수 있도록 원본 보존 |
| **감정은 리뷰별 호출, 키워드·요약은 묶음 1회 호출** | 감정은 건별 결과가 필요하지만, 종합 인사이트는 전체 맥락이 필요하고 비용도 절감됨 |
| **AI 응답을 JSON 형식 강제** | 자연어 파싱 대신 구조화 응답으로 파싱 실패 리스크 제거 |
| **중복 판정을 내용 해시로** | 리뷰에는 URL 같은 고유키가 없음 |
| **별점 이상치는 리뷰를 버리지 않고 별점만 비움** | 별점이 잘못 들어갔다고 리뷰 본문까지 버릴 이유는 없음 |
| **알림을 건수가 아닌 비율로 판정** | 리뷰 총량 증가와 불만 심화는 다른 상황 |
| **알림 기준일을 오늘이 아닌 데이터 마지막 날로** | 과거 데이터셋으로 돌려도 의미 있는 비교가 되도록 |
| **추이 기본 집계를 주 단위로** | 하루 2~3건 규모에서는 일 단위 선그래프가 톱니처럼 튀어 추세가 안 보임 |
| **pandas 대신 `csv` + `openpyxl`** | 의존성 최소화, CSV·Excel 읽기/쓰기에 충분 |
| **공통 옵션을 argparse `parents` 대신 선파싱** | `parents` 는 서브파서 기본값이 앞쪽 값을 덮어써 조용히 무시됨 |
| **`--mock` 모드** | API 키·비용 없이 전체 파이프라인 로직을 검증·시연 가능 |

---

## 보너스 과제

| 보너스 | 구현 |
|--------|------|
| **다국어 감정 분석** | `clean` 에서 한글/라틴 문자 비율로 `ko`/`en` 자동 판정 → 프롬프트에 언어 힌트 전달. 영어 리뷰도 동일 스키마로 분석되며 `list --lang en` 으로 조회 가능. 샘플 데이터에 영어 리뷰 8건 포함 |
| **감정 변화 알림** | `alert` 커맨드 + `dashboard` 리포트·HTML 에 자동 포함. 최근 N일 부정 **비율**을 직전 동일 기간과 비교해 임계 배수 초과 시 경고 |
| **HTML 대시보드** | `dashboard --html` — 차트 PNG 를 base64 로 **인라인 임베드**한 단일 HTML. 외부 CSS/JS/이미지 의존이 없어 파일 하나만 공유하면 그대로 열림 |
| **제품/카테고리별 비교** | `compare --by product|category` — 리뷰 수·평균 별점·감정 분포·부정 비율 비교표 + 해석 문장 + 비교 차트(`--chart`) |

---

## 문제 해결

**`OPENAI_API_KEY 가 없습니다`**
`.env` 파일이 프로젝트 루트에 있는지, `OPENAI_API_KEY=` 뒤에 값이 있는지 확인하세요.
급하면 `--mock` 으로 키 없이 전체 흐름을 돌려볼 수 있습니다.

**차트의 한글이 네모(□)로 나옴**
한글 폰트를 찾지 못한 경우입니다. macOS는 `AppleGothic` 이 기본 제공됩니다.
Linux 에서는 `sudo apt install fonts-nanum` 후 matplotlib 캐시를 지우세요
(`rm -rf ~/.cache/matplotlib`).

**`SyntaxError` 가 나면서 실행이 안 됨**
Python 3.9 이하로 실행했을 가능성이 큽니다. `python --version` 을 확인하고
3.10 이상 가상환경의 인터프리터(`.venv/bin/python`)로 실행하세요.

**`리뷰 텍스트 컬럼을 찾지 못했습니다`**
파일 헤더가 인식 목록에 없는 경우입니다. 에러 메시지에 파일의 실제 헤더와
인식 가능한 이름이 함께 나오니, `config.json` 의 `import.column_aliases.review_text`
에 해당 헤더명을 추가하세요.

**같은 파일을 다시 import 했더니 리뷰가 늘어남**
`import` 는 원본을 있는 그대로 쌓는 단계라 중복을 막지 않습니다.
중복은 `clean` 단계에서 정책(`skip` / `upsert`)에 따라 처리됩니다.

---

## 개발 기록

설계 문서와 단계별 진행 기록은 [`docs/PLAN.md`](docs/PLAN.md) 에 있습니다.
