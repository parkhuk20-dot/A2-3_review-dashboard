# 프로젝트: AI 기반 고객 리뷰 감정 분석 대시보드 (A2-3)

> 이 문서는 **작업 재개용 단일 진실 소스**입니다. 저장소(`docs/PLAN.md`)와 Obsidian 노트를 항상 같은 내용으로 유지합니다.

## 개요

- 목표: 고객 리뷰를 파일에서 수집 → 정제 → AI 감정 분석 → 키워드·요약 추출 → 대시보드 시각화·리포트·내보내기까지 잇는 **CLI 기반 리뷰 분석 서비스** (2026 Codyssey A2-3 과제)
- 상태: **구현·검증 완료** (필수 13/13, 보너스 4/4, OpenAI 실호출까지 검증 완료)
- 저장소: `~/Desktop/codyssey/A2-3_review-dashboard` → https://github.com/parkhuk20-dot/A2-3_review-dashboard (public)
- 실행 환경: Python 3.12 (`.venv`) — 시스템 python3는 3.9라 과제 요구(3.10+) 미달
- AI: OpenAI (`openai` SDK, `gpt-4o-mini`), 키는 `.env` 의 `OPENAI_API_KEY`
- 데이터 입력: **파일 기반만** (CSV / Excel). 크롤링은 과제 제약상 금지

## 문제

리뷰는 품질을 가늠하는 가장 직접적인 지표지만, 수백~수천 건을 사람이 읽는 건 비현실적이다.
단순 감정 분류에서 끝나면 의사결정에 쓸 수 없으므로 **시간에 따른 감정 추이 · 불만/칭찬 키워드 · 별점과 감정의 상관관계**까지 뽑아
비즈니스 인사이트로 연결하는 것이 목표다.

## 핵심 설계 원칙

- **단계 분리**: 각 서브커맨드는 독립 실행 가능, 이전 단계의 저장 결과를 입력으로 받음
- **raw / clean 분리**: 원본은 손대지 않고 보존, 정제 데이터는 별도 테이블
- **외부 의존성 격리**: AI API는 래퍼 뒤에 두어 교체 가능, `--mock` 으로 키 없이 전체 흐름 검증
- **실패 내성**: API 오류는 지수 백오프 재시도 후 로깅·스킵, 전체 실행은 멈추지 않음
- **재현 가능**: 이미 분석된 리뷰는 기본 스킵(캐싱)해 재실행 비용 최소화

## 기술 스택

| 항목 | 선택 | 비고 |
|------|------|------|
| 언어 | Python 3.10+ (실제 3.12) | 과제 요구 |
| CLI | `argparse` 서브커맨드 | 요구사항 명시 |
| 파일 입력 | `csv` (표준) + `openpyxl` | CSV + Excel 양쪽 지원. pandas 미사용(의존성 최소화) |
| 저장소 | **SQLite** (`sqlite3`) | 영구 저장, 조인·필터·집계 유리 |
| AI | **OpenAI** (`openai` SDK) | 감정·키워드·요약, JSON 응답 강제 |
| 시각화 | `matplotlib` | 한글 폰트 적용(AppleGothic 등 자동 탐지) |
| 내보내기 | `csv`, `json`, `openpyxl` | CSV + JSONL + Excel 3종 (요구 2개 이상) |
| 설정 | `config.json` + `.env` | API 키는 코드·config에 절대 미포함 |
| 로깅 | `logging` | INFO/WARNING/ERROR, 콘솔 + 파일 |

## 아키텍처

```
A2-3_review-dashboard/
├── main.py                  # 엔트리포인트: argparse 서브커맨드 라우팅
├── config.json              # 중복 정책, 정제 규칙, 시각화·알림 옵션 (키 미포함)
├── config.example.json      # 커밋용 예시
├── .env.example             # OPENAI_API_KEY 템플릿
├── requirements.txt
├── README.md
├── docs/PLAN.md             # 이 문서
├── data/
│   ├── reviews.db           # SQLite 영구 저장소 (gitignore)
│   └── sample_reviews.csv   # 샘플 리뷰 데이터 (커밋, 최소 30건 이상)
├── src/
│   ├── cli.py               # 서브커맨드 정의 & 옵션 파싱
│   ├── config.py            # 설정 로드 + .env 병합
│   ├── logger.py            # logging 설정
│   ├── retry.py             # 지수 백오프 재시도 데코레이터
│   ├── db.py                # SQLite 스키마·CRUD·upsert·조회
│   ├── importer.py          # CSV/Excel 읽기 → raw_reviews (+ add 수동 입력)
│   ├── cleaner.py           # 정제: 검증·정규화·별점·날짜·짧은 리뷰·중복
│   ├── ai/
│   │   ├── client.py        # OpenAI 래퍼 (JSON 강제, 재시도, mock 모드)
│   │   ├── sentiment.py     # analyze: 리뷰별 감정 + 신뢰도 점수
│   │   └── extractor.py     # extract: 키워드·요약·불만유형·개선제안
│   ├── viewer.py            # list / show / stats
│   ├── visualize.py         # matplotlib 차트 (한글 폰트)
│   ├── report.py            # 품질지표·TOP N·AI 인사이트 리포트
│   ├── exporter.py          # CSV / JSONL / Excel 내보내기
│   ├── alert.py             # [보너스] 부정 리뷰 급증 경고
│   ├── compare.py           # [보너스] 제품/카테고리별 비교 분석
│   └── html_dashboard.py    # [보너스] 단일 HTML 대시보드
└── output/{charts,reports,exports}/
```

**데이터 흐름**

```
[import] CSV/Excel 파일 → raw_reviews      ( [add] 수동 1건 입력도 같은 경로 )
   ↓
[clean] 검증·정규화·중복(skip/upsert) → clean_reviews
   ↓
[analyze] OpenAI 감정 분석(리뷰별) → sentiments
   ↓
[extract] OpenAI 키워드·요약·개선제안(묶음 1회 호출) → extractions
   ↓
[list/show/stats] 조회   [dashboard] 차트 3종 + 리포트   [export] CSV/JSONL/XLSX
                          [alert] [compare] [--html]  ← 보너스
```

## DB 스키마 (SQLite)

```sql
CREATE TABLE raw_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file TEXT,            -- 어느 파일에서 왔는지
  source_row INTEGER,          -- 원본 파일의 행 번호
  review_text TEXT,
  rating TEXT,                 -- 원본 그대로(문자열). 검증은 clean 단계에서
  review_date TEXT,            -- 원본 그대로
  product TEXT,
  category TEXT,
  raw_json TEXT,               -- 원본 행 전체 보존
  imported_at TEXT,
  is_cleaned INTEGER DEFAULT 0
);

CREATE TABLE clean_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_id INTEGER REFERENCES raw_reviews(id),
  review_hash TEXT UNIQUE,     -- 중복 판정 키(정규화 텍스트+제품 해시)
  review_text TEXT NOT NULL,
  rating INTEGER,              -- 1~5 검증 통과값, 없으면 NULL
  review_date TEXT,            -- YYYY-MM-DD 로 통일
  product TEXT,
  category TEXT,
  lang TEXT,                   -- [보너스] ko / en
  text_len INTEGER,
  cleaned_at TEXT,
  updated_at TEXT
);

CREATE TABLE sentiments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  review_id INTEGER UNIQUE REFERENCES clean_reviews(id),
  sentiment TEXT,              -- positive / negative / neutral
  score REAL,                  -- 신뢰도 0.0~1.0
  keywords TEXT,               -- 리뷰별 핵심어(쉼표 구분)
  model TEXT,
  analyzed_at TEXT
);

CREATE TABLE extractions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_sentiment TEXT, scope_product TEXT,
  date_from TEXT, date_to TEXT, n_reviews INTEGER,
  pos_keywords TEXT, neg_keywords TEXT,
  summary TEXT, complaint_types TEXT, suggestions TEXT,
  model TEXT, created_at TEXT
);

CREATE TABLE import_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file TEXT, total INTEGER, inserted INTEGER, skipped INTEGER,
  imported_at TEXT
);
```

## CLI 설계

| 서브커맨드 | 역할 | 주요 옵션 |
|-----------|------|----------|
| `import` | CSV/Excel 파일 수집 → raw | `--file`, `--sheet`, `--encoding`, `--limit` |
| `add` | 리뷰 1건 수동 추가 | `--text`, `--rating`, `--date`, `--product`, `--category` |
| `clean` | 정제 + 중복 처리 → clean | `--dedup {skip,upsert}`, `--min-length`, `--limit` |
| `analyze` | AI 감정 분석 | `--all`, `--id`, `--unanalyzed`, `--limit`, `--mock` |
| `extract` | AI 키워드·요약·개선제안 | `--sentiment`, `--product`, `--date-from/to`, `--limit`, `--mock` |
| `list` | 목록 조회 | `--sentiment`, `--rating`, `--rating-min/max`, `--date-from/to`, `--product`, `--lang`, `--page`, `--size`, `--sort` |
| `show` | 상세 조회 | `--id` |
| `stats` | 통계 요약 | `--product`, `--date-from/to` |
| `dashboard` | 차트 + 종합 리포트 | `--format {md,txt}`, `--top-n`, `--html`, `--no-charts` |
| `export` | 내보내기 | `--format {csv,jsonl,xlsx}`, `--sentiment`, `--rating-min`, `--date-from/to` |
| `alert` *(보너스)* | 부정 리뷰 급증 경고 | `--days`, `--threshold` |
| `compare` *(보너스)* | 제품/카테고리 비교 | `--by {product,category}`, `--products`, `--chart` |

## 기능 상세

- **import**: 확장자로 CSV/Excel 자동 판별. 헤더명을 유연 매핑(`review_text|리뷰|내용|content`, `rating|별점|평점`, `date|작성일`, `product|제품`). 원본 행을 `raw_json` 에 통째로 보존. 파일 단위 결과를 `import_log` 에 기록.
- **add**: 파일 없이 리뷰 1건을 직접 raw 에 넣는 경로. 테스트·데모용.
- **clean**: ① 필수 필드 검증(리뷰 텍스트 존재) ② 텍스트 정규화(HTML 태그·중복 공백·제어문자·이모지 정리) ③ 별점 1~5 범위 검증(벗어나면 NULL) ④ 날짜 다형식 파싱 → `YYYY-MM-DD` 통일 ⑤ 짧은 리뷰 필터링(`min_length`, 기본 10자) ⑥ 중복 `skip`/`upsert`(정규화 텍스트 해시). ⑦ [보너스] 한글 비율 휴리스틱으로 `lang` 판정.
- **analyze**: `--all`/`--id`/`--unanalyzed` 대상 선택, 이미 분석된 건 기본 스킵. JSON 응답 강제로 `{sentiment, score, keywords}` 파싱. API 실패는 로깅 후 스킵하고 계속 진행. `--mock` 은 별점·감정어 사전 기반 규칙으로 대체.
- **extract**: 조건(기간·감정·제품)으로 리뷰를 모아 **한 번의 호출**로 종합 분석 → `{pos_keywords, neg_keywords, summary, complaint_types, suggestions}`. `extractions` 에 저장해 대시보드에서 재사용.
- **list/show/stats**: 감정·별점·기간·제품·언어 필터 + 페이지네이션 + 정렬(`date`,`rating`,`score`). `stats` 는 총 리뷰 수, 분석 완료율, 감정 분포, 별점 분포, 평균 별점, 평균 감정 점수 출력.
- **dashboard**: 차트 3종 필수 — ① 감정 분포 ② 시간별 감정 추이(일/주 단위 선그래프) ③ 별점별 감정 분포(누적 막대). + [보너스] 제품별 비교 차트. 리포트에 품질 지표·TOP N·AI 추출 결과·[보너스] 급증 경고 포함, 콘솔 + MD/TXT 저장.
- **export**: CSV(utf-8-sig) / JSONL / XLSX 3종. 감정·최소 별점·기간 필터.
- **설정·로깅**: `config.json` 에 중복 정책·최소 길이·모델·TOP N·알림 임계치. API 키는 `.env` 전용. `logging` 3레벨(콘솔 + `logs/dashboard.log`).

**품질 지표(2개 이상 요구 → 4개)**: 정제 통과율(raw→clean), 감정 분석 완료율, 중복 제거 건수, **별점–감정 일치율**(★4~5=긍정 / ★1~2=부정 기준), 평균 신뢰도 점수.

**TOP N(1개 이상 요구 → 3개)**: 긍정 키워드 TOP N, 부정 키워드 TOP N, 제품별 평균 별점 TOP N.

## 보너스 과제 (4개 전부 반영)

| 보너스 | 반영 방식 |
|--------|----------|
| ① 다국어 감정 분석 | `clean` 에서 `lang`(ko/en) 자동 판정 → 프롬프트에 언어 힌트 전달, 영어 리뷰도 동일 스키마로 분석. `list --lang en` 필터, 샘플 데이터에 영어 리뷰 포함 |
| ② 감정 변화 알림 | `alert --days 7 --threshold 1.5` — 최근 N일 부정 비율을 직전 동일 기간과 비교해 배수 초과 시 경고. `dashboard` 리포트에도 자동 포함 |
| ③ HTML 대시보드 | `dashboard --html` — 차트 PNG를 base64로 **인라인 임베드한 단일 HTML** 1개 파일 생성(외부 의존 없음) |
| ④ 제품/카테고리별 비교 | `compare --by product` — 제품별 리뷰 수·평균 별점·감정 비율 표 + 비교 차트(그룹 막대) |

## 개발 단계 (체크리스트)

- [x] **0단계** 스캐폴딩 — config/logger/db/retry/CLI 골격 + 샘플 CSV 생성 → `73965b5`
- [x] **1단계** `import` / `add` — CSV·Excel 읽기, 헤더 유연 매핑, raw 저장 → `cba1ec8`
- [x] **2단계** `clean` — 정제 5규칙 + 중복 skip/upsert + 언어 판정 → `5c9d8fa`
- [x] **3단계** `analyze` — OpenAI 감정 분석, mock 모드, 캐싱 스킵 → `b8e78ed`
- [x] **4단계** `extract` — 묶음 1회 호출로 키워드·요약·개선제안 → `6a11e3c`
- [x] **5단계** `list` / `show` / `stats` — 필터·페이지네이션·정렬 → `f4cb609`
- [x] **6단계** `dashboard` — 차트 3종 + 품질지표·TOP N 리포트 → `9e62027`
- [x] **7단계** `export` — CSV / JSONL / XLSX → `f6d3ba4`
- [x] **8단계** 보너스 4종 — 다국어·급증 알림·HTML·제품 비교 → `db9ec7a`
- [x] **9단계** 통합 검증 · 실호출 검증 · README 마무리

> 원칙: 필수(1~13) 완주 후 보너스. **각 단계 완료 시 git 커밋 + 이 문서/Obsidian 노트 갱신**을 함께 진행한다.

## 리스크 & 대응

| 리스크 | 대응 |
|--------|------|
| 리뷰 파일의 헤더명·인코딩 제각각 | 헤더 유연 매핑 + `--encoding` 옵션, utf-8-sig 기본 |
| 리뷰 건수 많을 때 API 비용·시간 | 리뷰별 분석은 `--limit`·캐싱, 종합 추출은 묶음 1회 호출 |
| AI 응답 형식 흔들림 | JSON 응답 강제 + 라벨 정규화(긍정/positive/POS → positive) |
| 별점·날짜 결측/이상치 | 범위 검증 후 NULL 처리, 차트·집계에서 NULL 제외 명시 |
| 한글 폰트 깨짐 | OS별 폰트 자동 탐지, `axes.unicode_minus=False` |
| API 키 노출 | 키는 `.env` 만, `config.example.json`·`.env.example` 만 커밋, DB·output 은 gitignore |

## 요구사항 커버리지 체크리스트

**필수**
- [x] 1. argparse 서브커맨드 10종(import/add/clean/analyze/extract/list/show/stats/dashboard/export)
- [x] 2. CSV·Excel 수집, 필수/선택 필드, raw 저장소 저장
- [x] 3. 정제 5규칙 + 중복 skip/upsert + clean 별도 저장
- [x] 4. AI 감정(긍/부/중) + 신뢰도 0.0~1.0, `--all/--id/--unanalyzed`, 실패 스킵·기분석 스킵
- [x] 5. 조건별 종합 AI 추출, 항목 2개 이상(키워드·요약·개선제안·불만유형 = 4종), 별도 저장
- [x] 6. list 필터·페이지네이션·정렬 / show 상세 / stats 통계
- [x] 7. matplotlib 차트 3종 + 한글 폰트 + PNG
- [x] 8. 리포트: 품질지표 2개↑, TOP N 1개↑, AI 결과 포함, 콘솔 + TXT/MD
- [x] 9. 내보내기 2포맷 이상 + 필터 옵션
- [x] 10. config.json 설정 + logging 3레벨
- [x] 11. SQLite 영구 저장
- [x] 12. 모듈 4개 이상 분리
- [x] 13. 샘플 리뷰 CSV 30건 이상

**보너스**
- [x] 다국어(한국어 + 영어) 감정 분석
- [x] 최근 N일 부정 비율 급증 경고
- [x] 단일 HTML 대시보드
- [x] 제품/카테고리별 비교 분석

## 진행상황

### 2026-07-22 (계획 수립 · 환경 준비)

1. 과제 브리프 검토 후 개발 계획서 확정.
2. 저장소 위치를 `~/Desktop/codyssey/A2-3_review-dashboard` 로 결정(처음엔 A2-2 저장소 안에 폴더가 생성돼 중첩 repo가 될 뻔했음). `git init` 완료.
3. Python 3.12 venv 생성 + `openai`·`matplotlib`·`openpyxl` 설치 완료.
4. GitHub public 저장소 연결 예정(`gh` CLI 인증 확인됨).

### 2026-07-22 ~ 07-23 (0~9단계 구현 및 검증 완료)

5. **0~8단계를 단계별 커밋으로 구현 완료** (커밋 9개, 위 체크리스트의 해시 참고). GitHub public 저장소 연결 후 매 단계 push.
6. **샘플 데이터를 의도적으로 "지저분하게" 설계**: 110건 중 — 완전 중복 4건, 10자 미만 3건,
   별점 범위 이탈 2건(0점·7점), 별점/작성일/제품명 결측 각 1건, HTML 태그 포함 1건, 공백 과다 1건,
   날짜 표기 3종(`-`, `/`, `.`), 영어 리뷰 8건. → 정제 규칙 5종·중복 정책·다국어를 **실행으로 증명**할 수 있게 함.
   또 7월 첫째 주에 배송 지연/불량/고객센터 불만을 몰아넣어 **감정 급증 알림이 실제로 발화**하도록 구성.
7. **구현 중 발견·수정한 버그 2건**:
   - `analyze --id` 로 특정 리뷰를 지정해도 "이미 분석됨"으로 스킵되던 문제.
     기분석 스킵은 기본(`--unanalyzed`) 모드에서만 적용해야 하는데 `--all` 이 아닌 모든 모드에 걸려 있었음.
   - `sentiment_counts()` 에서 WHERE 절에 불필요한 `LEFT JOIN → JOIN` 문자열 치환이 남아 있던 것(무해했지만 혼란 소지).
8. **일 단위 추이 차트가 톱니처럼 튀는 문제**를 발견해 주 단위 집계(`--trend-unit week`)를 추가하고 기본값으로 삼음.
   하루 2~3건 규모에서는 일 단위 선그래프로 추세가 읽히지 않았음.
9. **통합 검증(별도 DB·설정으로 격리 실행)**: 서브커맨드 16개 조합 전부 정상 종료.
   오류 처리도 확인 — 없는 파일 / 리뷰 컬럼 없는 CSV / 지원 안 하는 확장자(.pdf) / 없는 리뷰 ID /
   조건 0건 / 페이지 범위 초과(마지막 페이지로 보정) / 빈 DB / 잘못된 옵션값 / 키 없음 모두
   **행동 지침이 담긴 메시지**와 함께 정상 종료.
10. **API 실패 내성 실측**: `OPENAI_BASE_URL` 을 잘못된 엔드포인트로 바꿔 실행 → 리뷰마다
    지수 백오프로 3회 재시도 후 `[ERROR] … 건너뜁니다` 로그를 남기고 다음 리뷰로 진행. 파이프라인 전체는 멈추지 않음.

### 2026-07-23 (OpenAI 실호출 검증 — mock 과의 차이 확인)

11. 먼저 2건으로 시험 호출(모델·JSON 응답 형식 확인) → **전체 103건 재분석, 성공 103 / 실패 0**.
12. **mock 과 실제 호출의 지표 차이가 뚜렷하게 드러남**:

    | 지표 | mock | 실제 (gpt-4o-mini) |
    |---|---|---|
    | 감정 분포 (긍/중/부) | 51 / 16 / 36 | **46 / 16 / 41** |
    | 평균 신뢰도 | 0.76 | **0.84** |
    | 별점–감정 일치율 | 100.0% | **90.0%** |

    mock 은 별점에서 감정을 파생시키므로 일치율 100%가 나오는 게 당연했고, 실제 호출에서는
    ★3에 부정 5건, ★4에 중립 5건이 섞이며 **별점만으로는 안 잡히는 10%를 본문이 드러냈다**.
    이것이 "별점이 있는데도 감정 분석이 필요한 이유"를 그대로 보여주는 근거가 됨.
13. **키워드 품질 차이**: mock 은 조사가 붙은 토큰("만에", "상태가", "요청했습니다")을 나열한 반면,
    실제 호출은 `배송 지연 / 불량 / 고객센터 / 포장 상태 / 가격 대비 성능` 처럼 **개념 단위**로 추출.
14. 실제 AI 추출 결과 예시 — 불만 유형: 배송 문제(12건), 제품 불량(10건), 고객 서비스(8건), 포장 문제(6건), 성능 문제(5건).
    개선 제안: "배송 시스템 개선 및 사전 안내 강화", "품질 검수 강화로 불량률 저감", "고객센터 응대 교육·시스템 개선".
15. 감정 급증 알림이 실제 데이터에서 발화: 최근 7일 부정 82.4% vs 직전 7일 46.2% = **1.78배 → 경고**.

## 결정 기록

| 날짜 | 결정 | 이유 |
|---|---|---|
| 2026-07-22 | 이전 과제(A2-2)와 별도의 독립 저장소로 분리 | 과제 단위 제출·이력 관리가 섞이지 않도록 |
| 2026-07-22 | pandas 대신 `csv` + `openpyxl` 사용 | 의존성 최소화, CSV·Excel 읽기/쓰기에 충분 |
| 2026-07-22 | 감정 분석은 리뷰별 호출, 키워드·요약은 묶음 1회 호출 | 감정은 건별 결과가 필요하고, 종합 인사이트는 전체 맥락이 필요 — 비용도 절감 |
| 2026-07-22 | 중복 판정 키를 "정규화 텍스트 + 제품" 해시로 | 리뷰는 URL 같은 고유키가 없어 내용 기반 판정이 필요 |
| 2026-07-22 | 별점–감정 일치율을 품질 지표에 포함 | 과제가 요구한 "별점과 감정의 상관관계"를 지표로 직접 드러냄 |
| 2026-07-22 | 공통 옵션(`--config`/`--verbose`/`--mock`)을 argparse `parents` 대신 선파싱으로 | `parents` 는 부모/서브파서가 같은 액션을 공유해, 서브커맨드 앞에 준 값이 조용히 무시됨 (이전 과제에서 겪은 문제를 처음부터 회피) |
| 2026-07-22 | 별점 이상치는 리뷰를 버리지 않고 별점 필드만 비움 | 별점이 잘못 입력됐다고 리뷰 본문까지 버릴 이유가 없음. 본문은 감정 분석에 그대로 쓸 수 있음 |
| 2026-07-22 | 감정 급증 판정을 건수가 아닌 **비율**로 | 리뷰 총량이 늘어난 것과 불만이 심해진 것은 다른 상황인데, 건수만 보면 구분되지 않음 |
| 2026-07-22 | 알림 기준일을 '오늘'이 아닌 **데이터의 마지막 작성일**로 | 샘플·과거 데이터셋으로 실행해도 의미 있는 비교가 되도록 |
| 2026-07-22 | 추이 차트 기본 집계를 주 단위로 | 하루 2~3건 규모에서 일 단위 선그래프는 톱니처럼 튀어 추세가 안 읽힘 (실측 후 변경) |
| 2026-07-22 | HTML 대시보드에 차트를 base64 인라인 임베드 | 파일 하나만 공유하면 그대로 열리도록. 외부 CSS/JS/이미지 의존 0 |
| 2026-07-23 | mock 결과를 실제 API 결과로 전부 교체 | 별점–감정 일치율 등 품질 지표가 mock/실제 혼합으로 왜곡되는 것을 방지 |
| 2026-07-23 | 대시보드 인사이트용으로 '전체' 범위 추출을 마지막에 실행 | 리포트는 가장 최근 추출을 인용하므로, 감정별 추출 뒤 전체 추출을 돌려 종합 요약이 실리게 함 |

## 링크

- 이전 부트캠프 프로젝트: [[뉴스 파이프라인 CLI]]
- 개념: [[Large Language Model]], [[Prompt Engineering]]
- 인덱스: [[Projects INDEX]]

## 재개용 요약 (다음 세션은 이것만 봐도 됨)

```bash
cd ~/Desktop/codyssey/A2-3_review-dashboard
.venv/bin/python main.py import --file data/sample_reviews.csv   # 수집
.venv/bin/python main.py clean                                    # 정제
.venv/bin/python main.py analyze --unanalyzed                     # AI 감정 분석
.venv/bin/python main.py extract --sentiment negative             # AI 키워드·요약
.venv/bin/python main.py dashboard --html                         # 차트 + 리포트 + HTML
```

- 실행은 반드시 `.venv/bin/python` (시스템 python3 는 3.9라 `str | None` 문법 미지원)
- **API 키는 이미 `.env` 에 있음** (gitignore 대상). `--mock` 을 붙이면 키 없이도 전체 흐름이 돌아감
- 코드: `main.py` + `src/` 16개 모듈. DB `data/reviews.db`, 산출물 `output/` — 둘 다 gitignore
- 사용법·설계 근거·문제 해결은 저장소 `README.md` 에 정리돼 있음
- 이 문서는 `docs/PLAN.md` 가 원본이고, `./scripts/sync_obsidian.sh` 로 옵시디언에 동기화됨

## 다음 행동 / 열린 질문

- [x] OpenAI 실호출 검증 완료 (2026-07-23, 103건 분석 + 추출 3회, 실패 0건)
- [x] 사용 모델 확정: `gpt-4o-mini` — JSON 응답 형식·한국어 품질 모두 문제 없음
- [x] GitHub public 저장소 연결 및 단계별 push 완료
- [ ] 제출 전 최종 확인: 채점 기준에 맞춰 README 데모 스크린샷/출력 예시를 더 넣을지 결정
- [ ] (선택) 리뷰 수가 수천 건 규모로 커질 경우 `analyze` 를 배치 호출로 묶는 최적화 검토
      (현재는 리뷰당 1회 호출 — 100건 규모에서는 문제없으나 선형 증가)
