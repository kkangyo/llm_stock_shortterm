# stock-news-bot

뉴스 기반 단타 자동화 툴의 1단계: **뉴스 수집 → Ollama 1차 필터 → (선택) Claude 2차 정밀분석**
파이프라인. 증권사 연동(실제 매수/매도)은 아직 포함하지 않았다.

## 아키텍처

```
[RSS 뉴스 수집기] → [Ollama 1차 필터: 관련성/감성 스크리닝] → (claude.enabled=true일 때만) [Claude 2차 분석: 종목/신뢰도 정밀판단] → [SQLite 저장 + 콘솔 알림]
```

- **Ollama (로컬)**: 모든 신규 뉴스에 대해 "특정 종목 관련 + 호재/악재/무관"을 빠르게 1차 분류.
  애매하면 관련 있음으로 넘겨서(recall 우선) 다음 단계로 넘긴다.
- **Claude API (선택, 기본 비활성화)**: `config/config.yaml`의 `claude.enabled: true`로 켜면
  Ollama가 걸러낸 후보에 대해서만 호출해서 종목 특정, 신뢰도, 선반영 여부 등을 구조화된
  JSON(tool use)으로 반환. API 사용량 기반 과금이 발생하므로 기본값은 꺼져 있다.
  꺼져 있으면 Ollama 1차 필터 결과만으로 후보를 표시한다 (종목명 미검증, 신뢰도 없음).
- **매매 로직은 아직 없음.** 콘솔에 강조 출력되고 SQLite에 기록될 뿐, 실제 주문은
  다음 단계(증권사 API 연동)에서 추가한다.

## 폴더 구조

```
config/config.yaml          # 폴링 주기, 장시간, RSS 피드, 모델명 등 설정
src/stock_news_bot/
  config.py                 # config.yaml + .env 로더
  models/schemas.py         # NewsItem / OllamaFilterResult / ClaudeAnalysisResult 등 pydantic 모델
  collectors/news_collector.py   # RSS 폴링 + 중복 제거
  llm/ollama_client.py      # 1차 필터
  llm/claude_client.py      # 2차 정밀분석 (tool use)
  storage/db.py             # SQLite 저장
  pipeline/news_pipeline.py # 위 컴포넌트를 엮는 오케스트레이터 + 장시간 판정
  main.py                   # CLI 진입점 (APScheduler)
data/                       # SQLite DB 저장 위치 (git에는 안 올라감)
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

### 3. Claude API 키 (선택 - 기본은 Ollama만 사용)

기본 설정(`claude.enabled: false`)에서는 이 단계를 건너뛰어도 된다. Claude 2차 분석을 쓰려면:

```powershell
Copy-Item .env.example .env
notepad .env   # ANTHROPIC_API_KEY=sk-ant-... 입력
```

[console.anthropic.com](https://console.anthropic.com) 에서 키 발급 (claude.ai 구독과는 별개의
사용량 기반 과금 서비스). 키를 넣은 뒤 `config/config.yaml`에서 `claude.enabled: true`로 변경.

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
  - `claude.enabled: false` (기본값) → `☆ 호재 후보 (Ollama 1차 필터만, 미검증)`
  - `claude.enabled: true` → Claude가 `buy_candidate`로 판단한 경우만 `★ 매수 후보 (Claude 확정)`

## 설정값 조정 (`config/config.yaml`)

| 항목 | 설명 |
|---|---|
| `polling.interval_seconds` | 뉴스 수집 주기 (초). 처음엔 30~60초 정도로 여유있게 시작 권장 |
| `news_sources.rss_feeds` | RSS 피드 목록. 현재 한국경제(경제/증권) 2개만 검증된 상태로 등록됨 |
| `news_sources.max_age_minutes` | 발행된 지 이 시간(분)이 지난 뉴스는 처리 대상에서 제외 (기본 60분). 단타 목적상 오래된 뉴스는 의미가 없어서 걸러내고, 남은 뉴스는 최신순으로 정렬해서 처리한다. 같은 값이 "재시작해도 최근에 이미 처리한 뉴스를 다시 후보로 띄우지 않기 위한 기억 기간"으로도 쓰인다 (SQLite 이력 기반) |
| `ollama.model` | `ollama pull`로 받은 모델명과 일치해야 함 |
| `claude.enabled` | Claude 2차 분석 사용 여부. 기본 `false` (Ollama만 사용, API 비용 없음) |
| `claude.min_confidence` | 이 값 미만 confidence는 매수 후보에서 제외 (현재는 로직에서 참고용, 실제 필터링은 추후 매매 엔진 단계에서 적용) |
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
