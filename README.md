# stock-news-bot

뉴스 기반 단타 자동화 툴의 1단계: **뉴스 수집 → 로컬 LLM 1차 필터 → (선택) 2차 정밀분석
(Claude/Gemini)** 파이프라인. 증권사 연동(실제 매수/매도)은 아직 포함하지 않았다.

## 아키텍처

```
[RSS 뉴스 수집기] → [1차 필터: 관련성/감성 스크리닝] → (analysis.enabled=true일 때만) [2차 정밀분석(Claude/Gemini): 종목/신뢰도 정밀판단] → [SQLite 저장 + 콘솔 알림]
```

- **1차 필터 (로컬, 무료)**: 모든 신규 뉴스에 대해 "특정 종목 관련 + 호재/악재/무관"을 빠르게
  1차 분류. 애매하면 관련 있음으로 넘겨서(recall 우선) 다음 단계로 넘긴다. 두 백엔드 중
  `local_filter.backend`(`config.yaml`)로 선택:
  - `ollama` (기본값) - CPU에서 Ollama 서버로 추론. 설치가 가장 간단함.
  - `openvino` - OpenVINO GenAI로 Intel Arc iGPU 또는 AI Boost NPU를 사용해 추론
    (Intel Core Ultra 등 NPU/iGPU 탑재 PC에 최적화). 아래 "OpenVINO 백엔드" 절 참고.
  - 뉴스 제목에 특정 기업이 없어도(예: 전쟁/분쟁, 자연재해, 정책 발표, 원자재 가격 급등)
    관련 테마의 실제 상장사를 최대 3개까지 추정해서 후보로 올린다 (예: 전쟁 뉴스 → 방산주).
  - 제목+RSS 요약만으로 종목을 못 찾으면(RSS 요약이 비어있는 경우가 잦음), 해당 뉴스에
    한해서만 기사 본문(`news.url`)을 가져와 다시 판단한다 (`collectors/article_fetcher.py`,
    trafilatura 사용). 유료 구독 전용 기사처럼 본문이 없으면 그대로 제외된다.
  - 두 경우 모두 최종적으로 KRX 상장 종목 목록과 대조해서 실재하지 않는 추정은 걸러낸다.
- **2차 정밀분석 API (선택, 기본 비활성화)**: `config/config.yaml`의 `analysis.enabled: true`로
  켜면 1차 필터가 걸러낸 후보에 대해서만 호출해서 종목 특정, 신뢰도, 선반영 여부 등을
  구조화된 형태로 반환. `analysis.backend`로 `claude`(유료, tool use) 또는 `gemini`(무료
  티어 제공, response_schema) 중 선택. 꺼져 있으면 1차 필터 결과만으로 후보를 표시한다
  (종목명은 KRX 목록으로 검증, 신뢰도는 없음).
- **매매 로직은 아직 없음.** 콘솔에 강조 출력되고 SQLite에 기록될 뿐, 실제 주문은
  다음 단계(증권사 API 연동)에서 추가한다.

## 폴더 구조

```
config/config.yaml          # 폴링 주기, 장시간, RSS 피드, 백엔드/모델 선택 등 설정
src/stock_news_bot/
  config.py                 # config.yaml + .env 로더
  models/schemas.py         # NewsItem / OllamaFilterResult / ClaudeAnalysisResult 등 pydantic 모델
  collectors/news_collector.py   # RSS 폴링 + 중복 제거
  llm/base.py                # 1차 필터 백엔드 공통 인터페이스
  llm/factory.py              # local_filter.backend 설정에 따라 백엔드 생성
  llm/prompts.py               # 1차 필터 백엔드 공통 프롬프트/JSON 스키마
  llm/ollama_client.py      # 1차 필터 - Ollama(CPU) 백엔드
  llm/openvino_client.py    # 1차 필터 - OpenVINO(Intel GPU/NPU) 백엔드
  llm/analysis_base.py        # 2차 분석 백엔드 공통 인터페이스
  llm/analysis_factory.py     # analysis.backend 설정에 따라 백엔드 생성
  llm/analysis_prompts.py     # 2차 분석 공통 프롬프트/유저메시지 조립
  llm/claude_client.py      # 2차 정밀분석 - Claude (tool use)
  llm/gemini_client.py      # 2차 정밀분석 - Gemini (response_schema, 무료 티어)
  matcher/                  # KRX 상장 종목 목록 캐싱 + 종목명 매칭
  storage/db.py             # SQLite 저장
  pipeline/news_pipeline.py # 위 컴포넌트를 엮는 오케스트레이터 + 장시간 판정
  main.py                   # CLI 진입점 (APScheduler)
data/                       # SQLite DB, KRX 종목 캐시 저장 위치 (git에는 안 올라감)
models/                     # OpenVINO IR 모델 다운로드 위치 (git에는 안 올라감)
logs/                       # 로그 파일
```

## 환경 셋업 (Windows / PowerShell)

### 1. Python 가상환경

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

### 2. Ollama 설치 및 모델 준비

[ollama.com](https://ollama.com) 에서 Windows 설치 프로그램을 받아 설치한 뒤:

```powershell
ollama pull qwen2.5:7b
```

Ollama는 설치 시 자동으로 백그라운드 서비스(기본 포트 11434)로 등록되므로 보통 `ollama serve`를
따로 실행할 필요는 없다. `config/config.yaml`의 `ollama.model` 값을 실제로 받은 모델명과 맞출 것.

**모델 선택: 왜 qwen2.5:7b인가?** 이 작업(짧은 한국어 뉴스에서 종목명 추출 + 호재/악재 판단)은
모델의 한국어 이해력이 품질을 좌우한다. Qwen2.5는 다국어/한국어 성능이 Llama 3.1보다 뚜렷하게
좋고 속도도 비슷한 7B급이라 기본값으로 택했다. 대안:
- `llama3.1:8b` - 가장 대중적이지만 한국어는 상대적으로 약함
- `exaone3.5:7.8b` - LG AI연구원의 한국어 네이티브 모델. 금융/뉴스 어휘에 더 강할 수 있음 (커뮤니티 사례는 적음)
- 더 큰 한국어 특화 모델(예: EEVE-Korean-10.8B)도 있지만 느려서 실시간 폴링에는 부담

`config.yaml`의 `ollama.model` 값만 바꾸면 되니 여러 개 받아서 같은 뉴스로 비교해보는 것도 좋다.

### 2-1. (선택) OpenVINO 백엔드 - Intel Arc iGPU / AI Boost NPU 가속

Ollama는 이 프로젝트 환경(Intel Core Ultra + Arc iGPU + AI Boost NPU)에서 **CPU로만** 돈다.
같은 하드웨어의 GPU/NPU를 실제로 쓰고 싶으면 OpenVINO GenAI 백엔드를 대신 쓸 수 있다.

```powershell
pip install -r requirements-openvino.txt
python scripts/download_openvino_model.py        # GPU용 Qwen2.5-7B-Instruct (INT4) 다운로드
# NPU로 시험해보고 싶다면 (1세대 NPU는 소형 모델만 안정적):
python scripts/download_openvino_model.py --npu  # NPU용 Qwen2.5-1.5B-Instruct (INT4) 다운로드
```

그다음 `config/config.yaml`에서:

```yaml
local_filter:
  backend: "openvino"

openvino:
  device: "GPU"    # NPU 모델을 받았다면 "NPU"로
  model_path: "models/openvino/qwen2.5-7b-instruct-int4-ov"   # NPU면 1.5b 경로로
```

**실측(이 PC, Qwen2.5-1.5B-Instruct-int4로 검증)**: GPU 디바이스는 뉴스 1건 분류에 약 5초,
NPU 디바이스는 약 14초(첫 호출은 NPU용 모델 컴파일 때문에 더 느림) 걸렸다. `openvino-genai`는
JSON 스키마를 강제하는 구조적 출력(`StructuredOutputConfig`)을 지원해서 Ollama의
`format="json"`보다 파싱 실패 가능성이 낮다.

**GPU vs NPU 트레이드오프**: GPU는 기존 Ollama와 같은 체급(7B)을 그대로 돌릴 수 있어 판단
품질 차이가 거의 없다. NPU(Intel AI Boost, 1세대)는 아직 대형 모델을 안정적으로 못 돌려서
1~2B급 소형 모델만 쓸 수 있는데, 실측해보면 이 체급은 7B보다 판단 품질이 눈에 띄게 떨어진다
(예: 명백한 수주 호재 뉴스를 관련 없음으로 오판하는 경우 확인됨). 일단은 **GPU를 기본값으로
권장**하고, NPU는 배터리 효율이 중요하거나 실험적으로 써보고 싶을 때 선택할 것.

### 3. 2차 정밀분석 API 키 (선택 - 기본은 로컬 1차 필터만 사용)

기본 설정(`analysis.enabled: false`)에서는 이 단계를 건너뛰어도 된다. 2차 분석을 쓰려면
`config/config.yaml`의 `analysis.backend`로 아래 둘 중 하나를 고르고:

**Claude (유료, 사용량 과금)**
```powershell
Copy-Item .env.example .env
notepad .env   # ANTHROPIC_API_KEY=sk-ant-... 입력
```
[console.anthropic.com](https://console.anthropic.com) 에서 키 발급 (claude.ai 구독과는 별개의
사용량 기반 과금 서비스).

**Gemini (무료 티어 제공)**
```powershell
notepad .env   # GEMINI_API_KEY=... 입력
```
[aistudio.google.com/apikey](https://aistudio.google.com/apikey) 에서 신용카드 없이 무료
발급. 2026년 기준 2.5 Flash는 분당 10회/일 500회 한도(이 파이프라인은 KRX 매칭까지 통과한
소수 후보에만 호출하므로 보통 이 안에 들어옴). **단, 무료 티어 데이터는 구글이 자사 제품
개선에 활용할 수 있다는 점은 알아둘 것** (유료 티어는 이 조항 없음).

키를 넣은 뒤 `config/config.yaml`에서 `analysis.enabled: true`, `analysis.backend: "claude"`
또는 `"gemini"`로 설정.

### 4. 실행

```powershell
# 장 시간 무시하고 1회 테스트 실행 (가장 먼저 이걸로 동작 확인)
python -m stock_news_bot.main --once --force

# 실제 운영: 장 시간(config.yaml 기준) 동안 폴링 주기마다 자동 반복
python -m stock_news_bot.main
```

정상 동작하면:
- 콘솔에 RichHandler 로그가 출력되고
- `logs/pipeline.log` 에 로그가 쌓이고
- `data/news_pipeline.db` (SQLite) 에 처리 결과가 기록되고
- 호재 후보가 나오면 콘솔에 강조 출력된다:
  - `analysis.enabled: false` (기본값) → `☆ 호재 후보 (1차 필터, 종목검증=...)`
  - `analysis.enabled: true` → 2차 분석이 `buy_candidate`로 판단한 종목마다
    `★ 매수 후보 (claude 확정)` 또는 `★ 매수 후보 (gemini 확정)`
    (뉴스 하나에 여러 종목이 걸리면 각각 따로 출력됨)

### 5. 유틸리티 스크립트

```powershell
python scripts/check_results.py    # 지금까지 저장된 판단 결과 조회
python scripts/reset_db.py         # data/news_pipeline.db 삭제 (확인 프롬프트 있음)
python scripts/reset_db.py --yes   # 확인 없이 바로 삭제
```

`reset_db.py`는 파이프라인 코드 어디에서도 자동 호출되지 않는다 - 이 스크립트를 직접
실행했을 때만 DB가 삭제된다. 삭제하면 처리 이력뿐 아니라 재시작 시 중복 처리를 막는 데
쓰이는 최근 뉴스 기록(`dedup_lookback_days`)도 같이 사라지므로, 삭제 직후 재시작하면
RSS가 다시 보여주는 최근 기사를 전부 "신규"로 재처리한다.

## 설정값 조정 (`config/config.yaml`)

| 항목 | 설명 |
|---|---|
| `polling.interval_seconds` | 뉴스 수집 주기 (초). 처음엔 30~60초 정도로 여유있게 시작 권장 |
| `news_sources.rss_feeds` | RSS 피드 목록. 현재 한국경제(경제/증권) 2개만 검증된 상태로 등록됨 |
| `news_sources.max_age_minutes` | 발행된 지 이 시간(분)이 지난 뉴스는 처리 대상에서 제외 (기본 60분). 단타 목적상 오래된 뉴스는 의미가 없어서 걸러내고, 남은 뉴스는 최신순으로 정렬해서 처리한다 |
| `news_sources.dedup_cache_size` | 런타임 중 중복 수집 방지를 위해 메모리에 유지할 최근 기사 id 개수 (기본 500) |
| `news_sources.dedup_lookback_days` | 재시작 시 DB에서 이 기간(일)치 news_id를 불러와 중복 재처리를 막음 (기본 2일) |
| `local_filter.backend` | 1차 필터 백엔드. `ollama`(기본) 또는 `openvino` |
| `ollama.model` | `ollama pull`로 받은 모델명과 일치해야 함 |
| `openvino.device` / `openvino.model_path` | `local_filter.backend: openvino`일 때 쓸 디바이스(`GPU`/`NPU`/`CPU`/`AUTO`)와 모델 경로 |
| `stock_matcher.aliases` | 뉴스에 흔히 쓰이는 이름이 KRX 상장명과 문자열상 다를 때 매핑 (예: 네이버→NAVER) |
| `analysis.enabled` | 2차 정밀분석 사용 여부. 기본 `false` (로컬 1차 필터만 사용, API 비용 없음) |
| `analysis.backend` | 2차 분석 백엔드. `claude`(기본, 유료) 또는 `gemini`(무료 티어 제공) |
| `analysis.min_confidence` | 이 값 미만 confidence는 매수 후보에서 제외 (현재는 로직에서 참고용, 실제 필터링은 추후 매매 엔진 단계에서 적용) |
| `claude.model` / `gemini.model` | 각 백엔드가 쓸 모델명 |
| `market_hours` | 장 시간 외에는 스케줄러가 폴링을 건너뜀 (`--force`로 무시 가능) |

## 알려진 제약 / 다음 단계

- **RSS 피드는 한국경제 2개만 실제 검증됨.** 연합뉴스/매일경제 등은 네트워크 환경에 따라
  접근이 막힐 수 있어 주석 처리해뒀다. 필요하면 `config.yaml`에서 직접 추가/검증할 것.
- **네이버 금융 실시간 속보처럼 RSS가 없는 소스**는 별도 크롤러가 필요하며, 이용약관/robots.txt
  확인이 선행되어야 한다.
- **매매 실행(증권사 API 연동)은 의도적으로 미구현.** 이 파이프라인이 안정적으로 종목/호재를
  잘 잡아내는지 먼저 검증한 뒤, 규칙 기반 매매 엔진(포지션 사이즈, 손절/익절, 쿨다운 등)과
  키움/나무 API 연동을 다음 단계로 진행한다.
- **동일 이슈의 중복 보도**는 URL 단위로만 중복 제거된다. 여러 매체가 같은 이슈를 다르게
  보도하는 경우까지 걸러내려면 제목 유사도 기반 dedup이 추후 필요하다.
