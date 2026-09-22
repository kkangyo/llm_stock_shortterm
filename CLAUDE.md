# CLAUDE.md

이 파일은 Claude Code가 이 저장소에서 작업을 시작할 때마다 자동으로 읽는 프로젝트 컨텍스트다.
설치/실행 방법은 [README.md](README.md)를 참고하고, 이 파일은 "왜 이렇게 만들었는지"와
"지금까지 뭘 했는지"에 집중한다. 새 기능을 제안하기 전에 이 문서, 특히 하단의
**구현 이력**과 **알려진 이슈/버릇**을 먼저 읽을 것 — 이미 시도했다가 버린 접근이거나,
이미 고쳐둔 버그를 다시 만들 수 있다.

## 프로젝트 한 줄 요약

한국 뉴스를 실시간으로 모니터링해서 특정 종목에 영향을 줄 만한 뉴스가 뜨면 매수 후보로
띄워주는 파이프라인. 최종 목표는 뉴스 기반 주식 단타(단기매매) 자동화지만, **실제 매수/매도
주문(증권사 연동)은 아직 의도적으로 미구현**이다. 뉴스 판단의 정확도/안정성을 먼저 검증하는
단계.

## 아키텍처 (현재 상태)

```
[RSS 뉴스 수집기]                     news_collector.py
  - 최신순 정렬, max_age_minutes 이내만, dedup(런타임+재시작) 적용
       ↓
[1차 필터: 로컬 LLM]                  llm/{ollama_client,openvino_client}.py (factory.py로 선택)
  - 관련성/감성/후보종목 판단, 테마주 추론 가능(전쟁→방산주 등)
       ↓ (실패 시) [본문 보강 재판단]  collectors/article_fetcher.py
  - 종목 매칭 실패한 뉴스에 한해서만 기사 본문을 가져와 같은 1차 필터로 재판단
       ↓
[KRX 실종목 검증]                     matcher/{krx_master,stock_matcher}.py
  - 후보 종목명이 실제 코스피/코스닥 상장사인지 대조, 매칭 안 되면 후보에서 제외
       ↓ (analysis.enabled=true일 때만)
[2차 정밀분석: Claude/Gemini]         llm/{claude_client,gemini_client}.py (analysis_factory.py로 선택)
  - 종목별(여러 개 가능) 신뢰도/선반영 여부/매수후보 여부 판단, 구조화 출력
  - 반환된 ticker+회사명도 다시 KRX와 대조(존재 여부 + 이름 일치) - 불일치 시 watch로 강등
       ↓
[SQLite 저장 + 콘솔/로그 알림]        storage/db.py, pipeline/news_pipeline.py
```

### 모듈 맵

```
config/config.yaml                       # 전체 설정
src/stock_news_bot/
  config.py                              # config.yaml + .env 로더 (Settings 싱글턴)
  timeutil.py                            # utcnow() - 전체 파이프라인 시간 기준을 naive UTC로 통일
  models/schemas.py                      # NewsItem/OllamaFilterResult/StockMatch/
                                          #   StockAssessment/ClaudeAnalysisResult/PipelineRecord
  collectors/
    news_collector.py                    # RSS 폴링, 신선도 필터, 런타임+재시작 dedup
    article_fetcher.py                   # 본문 보강용 스크래핑 (trafilatura)
  llm/
    base.py / factory.py / prompts.py    # 1차 필터: 공통 인터페이스(Protocol)/백엔드 선택/공유 프롬프트
    ollama_client.py                     # 1차 필터 - Ollama(CPU) 백엔드
    openvino_client.py                   # 1차 필터 - OpenVINO(Intel GPU/NPU) 백엔드
    analysis_base.py / analysis_factory.py / analysis_prompts.py  # 2차 분석: 위와 동일 패턴
    claude_client.py                     # 2차 정밀분석 - Claude (tool use, 여러 종목 반환)
    gemini_client.py                     # 2차 정밀분석 - Gemini (response_schema, 무료 티어)
  matcher/
    krx_master.py                        # KRX 전체 상장종목 목록 로딩+캐싱(FinanceDataReader)
    stock_matcher.py                     # 후보명 -> 실제 종목 매칭 (별칭/정확/부분/유사도/원문스캔)
  storage/db.py                          # SQLite 저장 (마이그레이션 포함)
  pipeline/news_pipeline.py              # 전체 오케스트레이션 + 장시간 판정
  main.py                                # CLI 진입점 (APScheduler, --backend/--device/--limit 오버라이드)
scripts/
  check_results.py                       # 저장된 판단 결과 조회
  reset_db.py                            # DB 삭제 (사람이 직접 실행할 때만 - 자동 호출 금지)
  download_openvino_model.py             # OpenVINO IR 모델 다운로드 (HF 사전 변환본)
```

## 핵심 설계 결정과 이유

- **로컬 LLM으로 대량 스크리닝 + Claude/Gemini로 소수만 정밀분석.** 기본값
  (`analysis.enabled: false`)은 꺼져 있음. Claude/OpenAI/Gemini 등 API를 제안할 때는 항상
  과금 방식(무료 티어라도 데이터 활용 조항 등)을 먼저 고지할 것 (사용자가 Claude 유료인 걸
  처음에 모르고 있었음). 2차 분석도 1차 필터처럼 교체 가능한 구조(아래 13번 항목).
- **1차 필터 백엔드는 교체 가능한 구조(factory 패턴).** Ollama(CPU)와 OpenVINO(Intel GPU/NPU)
  둘 다 `classify(news) -> OllamaFilterResult`라는 동일 계약을 따른다. 새 백엔드를 추가하려면
  `llm/base.py`의 Protocol을 구현하고 `llm/factory.py`에 분기만 추가하면 됨.
- **"LLM이 종목을 잘못 짚어도 괜찮다" - KRX 매칭이 안전망.** 1차 필터도 Claude/Gemini도
  존재하지 않는 종목명을 만들어낼 수 있고, 심지어 실존하는 티커에 엉뚱한 회사명을 붙이는
  경우도 있다(아래 13번 항목). 이 안전망 덕분에 프롬프트를 더 과감하게(테마주 추론 등)
  만들어도 리스크가 크지 않다는 게 이 프로젝트의 핵심 전제. 매칭 로직을 건드릴 때는
  오탐(존재하지 않는데 매칭됨)과 누락(존재하는데 매칭 안 됨) 둘 다 실측으로 확인할 것.
- **1차 필터는 recall 우선.** 애매하면 is_relevant=true로 다음 단계로 넘긴다. 최종
  필터링은 KRX 매칭 + 2차 분석 + (아직 미구현) 매매 엔진이 담당.
- **뉴스 하나 = 종목 하나가 아니다.** 전쟁 뉴스가 여러 방산주에 동시에 영향을 줄 수 있다는
  전제로 `ClaudeAnalysisResult.assessments`가 리스트다. 단일 종목 구조로 되돌리지 말 것.
- **매매 자동화(증권사 연동)는 사용자가 명시적으로 요청하기 전엔 먼저 제안하지 말 것.**
  뉴스 판단 파이프라인의 정확도/안정성 개선에 집중하는 단계라고 이미 합의됨.

## 구현 이력

각 항목은 "무엇을 왜" + 재발 방지용 규칙 위주로만 남김 (자세한 재현 과정은 git 로그/코드 참고).

### 1. (2026-09-17) 초기 스켈레톤
RSS → Ollama 1차 필터 → Claude 2차 분석 골격. 한국경제(경제/증권) 피드만 검증 - 일부
언론사가 User-Agent 없는 요청에 깨진 응답을 줘서 `requests`로 받은 뒤
`feedparser.parse(content)`로 넘김. Ollama는 시작 시 헬스체크(`GET /api/version`,
timeout=2s)로 서버 없으면 즉시 fallback (안 그러면 뉴스 100건마다 재시도로 수 분 지연).

### 2. (2026-09-17) Claude 비용 토글
Claude가 유료(사용량 과금)라는 걸 사용자가 몰랐음 → `claude.enabled` 추가, 기본 `false`.
Ollama 모델 `llama3.1:8b` → `qwen2.5:7b`(한국어 인식 개선).

### 3. (2026-09-19, `bcca69b`) 신선도 필터 + UTC 통일
`timeutil.utcnow()` 추가 - feedparser는 UTC로 정규화되는데 기존 코드가 로컬시간(KST)과
섞어 써서 9시간 오차가 나던 버그 수정. **파이프라인 전체에서 시간은 naive UTC로 통일 -
`datetime.now()` 대신 `timeutil.utcnow()`를 쓸 것.** `max_age_minutes`(발행 후 이 시간
지난 뉴스는 처리 제외, 현재 20분) 추가.

### 4. (2026-09-21) KOSPI/KOSDAQ 실종목 검증
1차 필터가 관련 있다고 해도 언급 종목이 실제 상장사가 아닐 수 있음(폭스바겐코리아/스타벅스
=비상장, 이스타항공=상장폐지, 국민은행=브랜드명≠상장사명 등).
- `krx_master.py`: FinanceDataReader(`fdr.StockListing('KRX')`)로 캐싱(`data/krx_master.csv`,
  24시간 갱신). **pip 패키지명은 `finance-datareader`(하이픈, 소문자)** - `FinanceDataReader`로
  설치 시도하면 실패함. "코스닥 우량기업부"가 `"KOSDAQ GLOBAL"`로 따로 표기돼 50개 종목이
  누락되던 버그도 수정(KOSDAQ으로 합침).
- `stock_matcher.py`: 별칭 → 정규화 완전일치 → 부분일치(길이비율 0.5 미만 제외 - "이닉스"
  오매칭 버그 방지) → difflib 유사도(cutoff 0.85). 동명이인 여러 개 걸리면 매칭 실패 처리.
- 매칭 실패 시 후보 제외(2차 분석도 스킵). 성공 시 검증된 종목명·티커를 근거로 전달하고,
  반환된 ticker도 다시 KRX와 대조해서 없으면 `watch`로 강등.

### 5. (2026-09-21) 재시작 dedup + 여러 종목 동시 판단
`dedup_lookback_days`(기본 2일)로 프로세스 재시작 시 DB에서 최근 news_id를 불러와 스킵
등록(런타임용 `dedup_cache_size` 롤링 캐시와는 별개 메커니즘, 둘 다 함께 동작).
`ClaudeAnalysisResult`를 단일 ticker/company_name에서 `assessments: list[StockAssessment]`로
재구성 - 하나의 뉴스가 여러 종목(방산주 등)에 동시에 영향을 줄 수 있다는 전제.

### 6. (2026-09-21) OpenVINO 백엔드 (Intel GPU/NPU 가속)
Ollama가 CPU만 써서, 이 PC(Core Ultra 7 155H + Arc iGPU + AI Boost NPU)의 GPU/NPU 활용을
위해 추가. GPU는 `Qwen2.5-7B`급도 무난(판단 품질 Ollama와 비슷, 5~12초/건), NPU는 1세대라
`Qwen2.5-1.5B`급만 안정적이고 품질 저하 확인됨 - **README/config 기본값은 GPU, NPU를 기본
으로 바꾸지 말 것.** **버그**: 기본 `GenerationConfig`가 `do_sample=True`라 비결정적
출력(같은 뉴스에도 다른 candidate_tickers) → `do_sample=False`로 고정. **이 설정을
건드리지 말 것.** `requirements-openvino.txt`로 별도 분리(기본 requirements.txt엔 미포함).
- **(2026-09-22 추가) `--once`로 반복 테스트할 때마다 모델 로딩이 30초 넘게 걸리는 문제.**
  원인은 GPU용 커널을 매번 새로 컴파일하고 있었던 것 - `LLMPipeline` 생성 시
  `CACHE_DIR`(모델 폴더 안에 `.ov_cache/`)을 지정해서 최초 1회 컴파일 후 디스크 캐시를
  재사용하도록 수정. 실측: 캐시 없음 31.6초 → 캐시 적중 7~9초(약 3~4배 단축). 캐시는
  ~4.4GB로 꽤 크지만 `models/`가 이미 `.gitignore` 대상이라 별도 조치 불필요. 디바이스나
  모델을 바꾸면 OpenVINO가 알아서 새 캐시 항목을 만든다(기존 캐시 삭제 불필요). **연속
  실행 모드(APScheduler)는 애초에 프로세스 하나가 계속 떠 있어서 이 로딩 자체가 세션당
  1번뿐이었다 - 이 문제는 `--once`로 반복 테스트할 때만 체감됨.**

### 7. (2026-09-21) DB 삭제 유틸리티
`scripts/reset_db.py` 추가. **파이프라인 코드 어디에서도 호출 안 함** - 사람이 직접 실행할
때만 삭제(기본 확인 프롬프트, `--yes`로 스킵 가능). WAL 파일도 같이 정리.

### 8. (2026-09-21) 본문 보강 재판단 + 테마주 추론
"매칭 실패로 제외되는 뉴스가 많다" + "전쟁 뉴스처럼 특정 기업 언급 없어도 테마 종목을
추론했으면" 요청 반영.
- `article_fetcher.py`: 매칭 실패한 뉴스에 **한해서만** trafilatura로 본문 보강 후 같은
  1차 필터로 재판단. 한경 전용 정리 로직 포함(다른 언론사 추가 시 손봐야 할 수 있음), 정리 후
  50자 미만이면 스킵.
- 프롬프트에 테마주 추론 지시 추가 - 전쟁/재해/정책/원자재 등 사건성 뉴스는 특정 기업 언급
  없어도 관련 테마의 실제 상장사를 최대 3개까지 추정 가능(KRX 재검증이 안전망이라 과감해도
  안전).
- **버그**: LIG넥스원이 LIG디펜스앤에어로스페이스로 상호 변경(079550 동일) - LLM은 옛
  이름을 계속 말해서 별칭 추가. **매칭 실패를 볼 때 "진짜 비상장"인지 "상호 변경"인지부터
  KRX 목록에서 확인할 것.**

### 9. (2026-09-21) 수동 테스트용 `--limit` 옵션
`--once --force`가 RSS 백로그 전체를 처리하느라 오래 걸리는 문제 반영. `--once` 기본
20건, 연속 실행 모드는 기본 무제한. `--limit N`으로 두 모드 다 오버라이드 가능.

### 10. (2026-09-21) 악재 스킵 + 로그 누락 수정 + 테마 추론 정밀도 보강
- **악재 조기 스킵**: `sentiment==negative`면 종목 매칭/본문 보강/2차 분석을 전부 스킵(매수
  후보 탐색이 목적이라 악재는 종목 특정해도 무의미 - 속도 개선 효과 큼).
- **로그 누락 버그**: `console.print()`는 `logging` 모듈을 안 거쳐서 화면에는 알림이 떠도
  `logs/pipeline.log`엔 안 남았음 → `console.print()` 직전에 `logger.info()`를 같이 추가.
  **새 알림을 console.print()로 추가할 때는 logger.info()도 반드시 같이 넣을 것.**
- **테마 추론 오탐**: 인물 동정(교수 수상 등) 기사에 산업 키워드가 스치듯 언급된 것만으로
  KCC/GS 같은 무관한 대기업이 매칭되던 사고 → "실제 사건에만 테마 추론 적용, 인물
  동정/인터뷰/행사 참석 기사는 제외"라는 가드레일과 "이 뉴스가 없었다면 오늘 주가가
  움직였을까?" 자문 기준 추가.

### 11. (2026-09-21) 종목명 할루시네이션 방지 - 원문 대조 이중 안전장치
실제 사례: "삼성전기·삼화콘덴서" 뉴스에서 1차 필터가 원문의 "삼화콘덴서"를 "삼화나노기술"로
잘못 재현하고, 있지도 않은 "삼성전자"를 지어냄. **GPU 추론은 `do_sample=False`로 고정해도
완전히 결정적이지 않다**(부동소수점 연산 순서 차이로 같은 입력에 다른 토큰이 나올 수 있음) -
종목 매칭 이상 현상을 디버깅할 때는 먼저 "같은 입력을 여러 번 넣었을 때 결과가 바뀌는지"부터
확인할 것.
- **recall 보강**: `find_literal_mentions()` - LLM 판단과 무관하게 원문에 실제 KRX 상장사명이
  글자 그대로 있는지 직접 스캔(이름 3자 미만은 오탐 위험으로 제외, 동명이인 있으면 보류).
- **precision 보강("직접 언급" vs "테마 추정" 자체 태깅)**: `candidate_tickers` 안에서 테마
  추정 종목명 앞에만 `~` 접두사를 붙이는 방식 채택. (처음엔 `thematic_tickers`를 별도
  배열로 분리해서 이중 기입시켰는데 **소형 모델(7B)의 테마 추론 품질을 눈에 띄게 떨어뜨림**
  - 스키마를 무겁게 만들지 말 것.) 태그 없는(=직접 언급 주장) 후보만 원문에 실제로 그
  이름이 있는지 대조해서, 없으면 할루시네이션으로 버림. `~` 태그 붙은 테마 추정 종목은
  원문에 없는 게 정상이므로 KRX 매칭만 되면 채택.
- 재현 테스트로 "삼성전자"(원문에 없음)는 자동 제외, "삼화콘덴서"(원문 직접 스캔)는 복구되어
  최종적으로 정확히 삼성전기+삼화콘덴서만 남는 것 확인. **한계**: LLM이 애초에 `~` 태깅
  자체를 빠뜨리는 경우까지는 못 잡음 - 발견하면 프롬프트 표기 규칙을 더 명확히 할 것.

### 12. (2026-09-22) 제약/바이오 뉴스 - 약물 브랜드명 별칭 시드
회사명 없이 약물 브랜드명만 나오는 뉴스(예: "케이캡, 시장 점유율 1위")에서 종목을 놓치는
gap 발견. 프롬프트에 "브랜드명이면 개발사를 추정하라" 지시를 추가해봤지만 **로컬 7B 모델의
한국 의약품 브랜드-개발사 매핑 지식 자체가 부족**해서 실제 회사명을 못 채움 - **모델 지식
한계는 프롬프트로 못 고친다.** 대신 `stock_matcher.aliases`(config.yaml)에 결정적 매핑을
시드로 추가(케이캡→HK이노엔, 램시마 등 다수 →셀트리온, 로수젯→한미약품 등 - 목록은
config.yaml 참고). 확신 없는 매핑(자회사 관계 등)은 넣지 않음 - **틀린 매핑으로 엉뚱한
종목을 올리는 것보다 안 올리는 게 낫다는 원칙.** **다음에 비슷한 도메인(반도체/자동차 부품
브랜드명 등)에서 같은 패턴을 보면, 프롬프트를 고치기 전에 "모델 지식 한계인지 지시 문제인지"
부터 구분할 것.**

### 13. (2026-09-22) Gemini 2차 분석 백엔드 추가 + 실제 호출 검증
Claude와 교체 가능한 구조로 Gemini 추가(`analysis_base/factory/prompts.py` 패턴,
`google-genai` SDK, `response_schema=list[PydanticModel])`로 구조화 출력). 무료 티어지만
구글이 자사 제품 개선에 데이터를 활용할 수 있다는 조항이 있음(유료 티어는 없음) - 이런
"무료지만 조건 있는" 서비스도 유료 API처럼 먼저 고지할 것. `config.yaml`을
`analysis:`(enabled/backend/min_confidence, 공통) + `claude:`/`gemini:`(백엔드별 세부)로
분리 - **`settings.claude_enabled`는 `analysis_enabled`로 개명됨, 옛 이름 쓰지 말 것.**

처음 구현 시엔 API 키가 없어 실제 호출 검증을 다음 세션으로 미뤘는데, 이어서 진행하다가
버그 3개 발견/수정:
- **`.env.example`(git 추적 파일)에 실제 API 키가 잘못 들어가 있었음** - `.env`(gitignore
  대상)로 옮기고 예시 파일은 빈 값으로 복구. **`.env.example`에 값이 채워진 줄이 보이면
  실키가 잘못 들어간 건 아닌지부터 의심할 것.**
- **`gemini-2.5-flash` 모델명이 이 API 키엔 404**("no longer available to new users") -
  Gemini 라인업이 빠르게 3.x대로 넘어가면서 신규 키는 구세대 모델이 막혀 있었음.
  `client.models.list()`로 확인 후 교체. **`gemini-flash-latest` 별칭은 피할 것** - 가리키는
  최신 모델(3.8-flash)이 무료 티어 한도가 더 빡빡하고(분당 5회) 503도 잦아서 오히려
  불안정함(4번 중 4번 실패). 대신 `gemini-3.6-flash`로 명시 고정(4번 중 3번 성공). **최신
  모델일수록 무료 티어가 더 안정적이라는 보장은 없다.** 이 모델도 언젠가 막힐 수 있으니
  404가 뜨면 다시 `list()`로 확인할 것 - 순전히 벤더 라인업 변경 문제.
- **티커 재검증이 "존재 여부"만 보고 "회사명 일치"는 안 봐서 안전망에 구멍이 있었음.**
  실측: Gemini가 `흥구석유`라는 실존 회사명에 `002680`(실제로는 `한탑`, 완전히 다른 회사)
  티커를 붙여 반환 - 기존 로직은 "KRX에 존재하는 티커인가"만 봐서 `ticker_verified=True`로
  통과했을 것. "존재하지 않는 이름"보다 훨씬 위험한 할루시네이션(무관한 종목이 매수 후보로
  올라감). `news_pipeline.py`에서 티커가 KRX에 있어도 `normalize_name(row.name)`과
  `assessment.company_name`이 다르면 `ticker_verified=False`+`watch`로 강등하도록 수정.
  **테마 추정 결과(11번 항목)일수록 이 회사명 대조가 특히 중요함** - 같은 재현 케이스에서
  테마 추정 중 티커 오류가 나왔음.
- **로그에 `[1차 통과]`만 보이고 2차 분석 결과가 안 보인다는 지적** - 원인은
  `recommended_action==buy_candidate`일 때만 로그를 남기던 것. 실제로는 2차 분석이 정상
  호출/저장되고 있었는데 결과가 `watch`/`ignore`면 그 사실 자체가 로그에서 사라졌음.
  `record.claude_result` 저장 직후 결과와 무관하게 항상 남는 요약 로그
  (`[2차 분석 완료:{backend}] 종목(action, confidence), ...`)를 추가. **"특정 조건에서만"
  로그를 남기는 코드를 추가할 때는, 조건에 안 걸리는 경우에도 "이 단계는 실행됐다"는
  최소 흔적을 남길지 항상 같이 고민할 것** (10번 항목과 같은 종류의 문제의 변주).
- 세 버그 수정 후 판단 품질 자체는 양호함을 확인(근거 없는 종목 추가 안 함, 루머는 보수적으로
  빈 배열, 테마 추정 정상 동작). **참고**: Gemini 호출이 예외로 실패하면
  `confidence=0.0/ignore`인 폴백을 반환하는데(`min_confidence` 미만이라 오탐 위험은 없음),
  DB만 봐서는 "API 실패"와 "판단 결과 무관"이 구분 안 되니 이상 현상 디버깅 시
  `logs/pipeline.log`의 "Gemini 분석 실패" 로그를 같이 대조할 것.
- **실사용 중 503(고수요) 오류로 정상 후보가 조용히 ignore 처리되는 것 발견** - 원인은
  `google-genai` SDK가 `http_options`를 안 주면 기본적으로 **재시도를 아예 안 함**
  (`stop_after_attempt(1)`). 그래서 구글 쪽 일시적 과부하 한 번에도 바로 위 폴백으로
  떨어지고 있었다. `GeminiAnalyzer.__init__`에서 `genai.Client(...,
  http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=3,
  initial_delay=2.0, max_delay=15.0)))`로 429/5xx에 지수 백오프 재시도를 붙여서 해결
  (SDK가 이미 지원하는 기능이라 직접 재시도 로직을 짤 필요는 없었음). **SDK를 새로 도입할
  때는 기본 재시도 정책이 뭔지부터 확인할 것** - "당연히 몇 번은 재시도해주겠지"라고
  가정하면 안 된다.

### 14. (2026-09-22) buy_candidate 여러 개 동시 발생 시 우선순위 랭킹 (점수 계산 + 로그까지만)
"매수 후보가 2개 이상 나오면 결국 하나를 골라서 매매를 진행할 건데, 어떤 근거로 고를지"
질문에서 시작. 시나리오가 두 가지임을 사용자와 확인: ①한 뉴스에서 여러 종목이 동시에
buy_candidate로 나오는 경우(MLCC 뉴스 → 삼성전기+삼화콘덴서) ②서로 다른 뉴스에서 나온
후보끼리 비슷한 시간에 겹치는 경우(자본/동시보유 한도 문제) - 결국 최종적으로는 하나만
골라 매수할 거라 두 경우 다 하나의 점수 체계로 비교해야 함. 일반적인 이벤트 드리븐 트레이딩
자료를 찾아보니 신뢰도/유동성/상관관계 세 축을 강조하는데, **이 파이프라인은 시세/거래량
데이터가 전혀 없어서 유동성 축은 반영 불가능** - 지금 있는 데이터만으로 1단계를 구현하고,
유동성 필터링(FinanceDataReader로 시가총액/거래대금 확인)은 사용자 확인 결과 다음 단계로
미룸.
- `pipeline/ranking.py`: `score_assessment(assessment, matched_stocks)` - confidence를
  기준으로 `is_already_priced_in=true`면 크게 감점(0.3배), `None`(모름)이면 약하게
  감점(0.85배), assessment의 ticker가 1차 필터의 `matched_stocks`(=KRX 매칭 목록)에 없으면
  2차 분석이 스스로 테마 추정해서 추가한 종목으로 보고 감점(0.7배 - 13번 항목에서 확인한
  테마 추정 쪽 할루시네이션 위험을 반영), KRX 매칭 방식이 `fuzzy`였으면 추가 감점(0.9배).
  **`StockAssessment`엔 "직접 언급/테마 추정" 플래그가 따로 없어서**, `assessment.ticker`가
  `matched_stocks`(1차 필터+KRX 매칭 결과) 목록에 있는지 여부로 대신 판별한다 - 새 필드
  없이 기존 스키마만으로 충분했음.
- 비교 대상 시간창은 `config.yaml`의 `ranking.window_minutes`(기본 30분, 사용자가 명시적
  으로 "고정 시간창" 방식을 선택함 - "장 열려있는 동안 계속 누적" 방식은 다음에 필요하면
  추가) - `storage/db.py`의 `get_recent_buy_candidates(since)`로 그 시간 안에
  buy_candidate가 하나라도 있었던 뉴스를 DB에서 원본 JSON째로 가져온다(스키마 모델
  파싱은 호출 쪽인 `news_pipeline.py`에서 함 - storage 계층이 도메인 모델을 몰라도 되게).
- `news_pipeline.py`의 `_log_priority_ranking()` - 현재 뉴스의 buy_candidate들 + DB에서
  가져온 최근 buy_candidate들을 합쳐서 점수 내림차순 정렬 후
  `[우선순위:최근N분,M건] 1위 종목(티커) score=... | 뉴스제목 / 2위 ...` 형태로 로그.
  2건 이상이면 콘솔에 현재 1위를 별도로 강조 표시. **아직 이 순위로 실제 매매를 자동
  결정하지 않는다 - 사람이 최종 선택할 때 참고하는 의사결정 보조 정보까지만.** 실측: 합성
  뉴스 2건(MLCC 2종목 + 반도체 보조금 1종목)을 순서대로 처리시켜서 같은 뉴스 내
  다중후보(2건)와 다른 뉴스 간 교차비교(3건, 이전 뉴스가 이미 DB에 저장된 걸 정상적으로
  끌어옴)를 둘 다 확인. **이 작업 범위는 사용자가 명시적으로 "점수 계산 + 로그/콘솔
  표시까지만"으로 한정함** - 유동성 필터링이나 실제 매매 자동 선택은 이 항목의 범위 밖.

## 알려진 이슈 / 버릇

- **이 Windows 환경 콘솔은 레거시 코드페이지(cp1252)**라 `rich` 콘솔/로그 출력이나 일반
  `print()`가 한글을 만나면 `UnicodeEncodeError`를 낸다. `rich` 로깅은 안 죽고 "Logging
  error"만 찍히지만(로그 파일에는 UTF-8로 정상 기록됨), 일반 스크립트(`scripts/*.py`)는
  상단에 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`를 넣어야 안전하다.
- **하드웨어**: Intel Core Ultra 7 155H(Meteor Lake), Intel Arc Graphics(iGPU), Intel(R)
  AI Boost(NPU). `openvino.Core().available_devices`에 CPU/GPU/NPU 모두 정상 인식됨.
- RSS 피드는 한국경제(경제/증권) 2개만 실제 검증됨. 연합뉴스/매일경제 등은 주석 처리만
  해둔 상태(미검증). 새 피드 추가 시 `article_fetcher.py`의 한경 전용 정리 로직이 다른
  언론사엔 안 맞을 수 있음을 감안할 것.
- 동일 이슈의 중복 보도는 URL 단위로만 dedup된다. 여러 매체가 같은 이슈를 다르게 보도하는
  경우까지 걸러내려면 제목 유사도 기반 dedup이 추후 필요함 (미구현).
- `analysis.enabled`는 기본 `false`, backend는 `claude`(유료) 또는 `gemini`(무료 티어+데이터
  활용 조항). 관련 기능을 테스트/시연할 때는 비용/조건을 먼저 언급할 것. Gemini 모델명은
  벤더 쪽에서 자주 바뀌니 404가 뜨면 `client.models.list()`로 재확인(13번 항목 참고).

## 다음 단계 (예정, 미구현)

1. 매매 의사결정 엔진 (규칙 기반: 포지션 사이즈/손절익절/쿨다운/동시보유제한)
2. 증권사 API 연동 (키움 REST 또는 나무증권), 모의투자로 최소 2~4주 검증 후 소액 실계좌 진행

이 두 단계는 **사용자가 명시적으로 요청하기 전엔 먼저 제안하지 말 것.**
