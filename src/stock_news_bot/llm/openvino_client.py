"""OpenVINO GenAI 기반 1차 필터 - Intel Arc iGPU / AI Boost NPU를 로컬 추론에 사용한다.

Ollama는 이 프로젝트 환경(Intel Core Ultra, Arc iGPU, AI Boost NPU)에서 CPU로만
돌아간다. OpenVINO 런타임은 같은 하드웨어에서 GPU/NPU 디바이스를 직접 지정해
추론할 수 있어 그 대안으로 추가한 백엔드다.

사전 준비: `python scripts/download_openvino_model.py`로 OpenVINO IR(INT4) 변환
모델을 미리 받아둬야 한다 (config.yaml의 openvino.model_path 경로). optimum-cli로
직접 변환할 필요 없이 Hugging Face에 이미 변환되어 올라온 모델을 그대로 받는다.

NPU(Intel AI Boost)는 이 세대(Meteor Lake) 기준으로 지원되는 모델이 제한적이고
(예: Qwen2.5-1.5B-Instruct급 소형 모델), 7B급은 GPU 디바이스가 훨씬 안정적이다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from stock_news_bot.config import settings
from stock_news_bot.llm.prompts import FILTER_JSON_SCHEMA, FILTER_SYSTEM_PROMPT, parse_tagged_tickers
from stock_news_bot.models.schemas import NewsItem, OllamaFilterResult, Sentiment

logger = logging.getLogger(__name__)


class OpenVinoFilter:
    def __init__(self, model_path: Path | None = None, device: str | None = None):
        self.model_path = model_path or settings.openvino_model_path
        self.device = device or settings.openvino_device
        self.pipe = None
        self._structured_output_config = None
        self.available = self._load()

    def _load(self) -> bool:
        if not self.model_path.exists():
            logger.warning(
                "OpenVINO 모델 경로(%s)가 없습니다. 먼저 "
                "'python scripts/download_openvino_model.py'로 모델을 받으세요. "
                "모든 뉴스를 1차 필터 통과 실패로 처리합니다.",
                self.model_path,
            )
            return False

        try:
            import openvino_genai as ov_genai
        except ImportError:
            logger.warning(
                "openvino-genai 패키지가 설치되어 있지 않습니다. "
                "'pip install -r requirements-openvino.txt'로 설치하세요. "
                "모든 뉴스를 1차 필터 통과 실패로 처리합니다."
            )
            return False

        try:
            # OpenVINO는 기본적으로 GPU용 컴파일된 커널을 매번 새로 빌드한다 - CACHE_DIR을
            # 지정하면 최초 1회만 컴파일하고 디스크에 캐싱해서 이후 로딩은 재사용한다.
            # 실측(2026-09-22): 캐시 없이 31.6초 -> 캐시 적중 시 7.0초 (약 4.5배 단축).
            # 캐시는 모델 파일 옆에 두고 models/ 전체가 이미 .gitignore 대상이라 별도
            # 처리 불필요. 디바이스/모델이 바뀌면 OpenVINO가 알아서 새 캐시 항목을 만든다.
            cache_dir = self.model_path / ".ov_cache"
            self.pipe = ov_genai.LLMPipeline(
                str(self.model_path), self.device, CACHE_DIR=str(cache_dir)
            )
            self._structured_output_config = ov_genai.StructuredOutputConfig(
                json_schema=json.dumps(FILTER_JSON_SCHEMA)
            )
            logger.info(
                "OpenVINO GenAI 로딩 완료 (device=%s, model=%s)", self.device, self.model_path
            )
            return True
        except Exception:
            logger.exception(
                "OpenVINO GenAI 로딩 실패 (device=%s, model=%s) - "
                "드라이버/런타임 설치 상태를 확인하세요. 모든 뉴스를 1차 필터 통과 실패로 처리합니다.",
                self.device,
                self.model_path,
            )
            return False

    def classify(self, news: NewsItem) -> OllamaFilterResult:
        if not self.available:
            return OllamaFilterResult(
                news_id=news.id,
                is_relevant=False,
                sentiment=Sentiment.NEUTRAL,
                reason="openvino 모델 로딩 실패",
            )

        user_content = f"제목: {news.title}\n요약: {news.summary}"

        try:
            config = self.pipe.get_generation_config()
            config.max_new_tokens = settings.openvino_max_new_tokens
            config.structured_output_config = self._structured_output_config
            # 기본값은 do_sample=True, temperature=0.7이라 같은 뉴스에도 매번 다른
            # candidate_tickers가 나올 수 있다. Ollama(temperature=0)와 동일하게
            # 결정적으로 판단하도록 그리디 디코딩으로 고정한다.
            config.do_sample = False

            self.pipe.start_chat(system_message=FILTER_SYSTEM_PROMPT)
            try:
                output = self.pipe.generate(user_content, config)
            finally:
                self.pipe.finish_chat()

            data = json.loads(str(output))
            candidate_tickers, thematic_tickers = parse_tagged_tickers(
                data.get("candidate_tickers", []) or []
            )
            return OllamaFilterResult(
                news_id=news.id,
                is_relevant=bool(data.get("is_relevant", False)),
                sentiment=Sentiment(data.get("sentiment", "neutral")),
                candidate_tickers=candidate_tickers,
                thematic_tickers=thematic_tickers,
                reason=data.get("reason", ""),
            )
        except Exception:
            logger.exception("OpenVINO 분류 실패 (news_id=%s) - 안전하게 relevant=False 처리", news.id)
            return OllamaFilterResult(
                news_id=news.id,
                is_relevant=False,
                sentiment=Sentiment.NEUTRAL,
                reason="openvino 호출 실패",
            )
