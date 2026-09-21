"""실행 진입점.

사용 예:
  python -m stock_news_bot.main --once            # 1회만 실행 (테스트용)
  python -m stock_news_bot.main --once --force     # 장 시간 무시하고 1회 실행
  python -m stock_news_bot.main                    # 장 시간 동안 주기적으로 실행

  # 1차 필터 백엔드를 config.yaml 수정 없이 일회성으로 바꿔서 실행
  python -m stock_news_bot.main --once --force --backend openvino --device GPU
  python -m stock_news_bot.main --once --force --backend openvino --device NPU \
      --model-path models/openvino/qwen2.5-1.5b-instruct-int4-ov
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from stock_news_bot.config import settings
from stock_news_bot.pipeline.news_pipeline import NewsPipeline, is_market_open, setup_logging

logger = logging.getLogger(__name__)


def run_forever(pipeline: NewsPipeline, force: bool) -> None:
    scheduler = BlockingScheduler(timezone=settings.market_timezone)

    def job():
        if not force and not is_market_open():
            logger.debug("장 시간 아님 - 스킵")
            return
        pipeline.run_once()

    scheduler.add_job(job, "interval", seconds=settings.polling_interval_seconds)
    logger.info(
        "파이프라인 시작 - %d초 간격, 장시간(%s~%s, %s) 동안 동작",
        settings.polling_interval_seconds,
        settings.market_start,
        settings.market_end,
        settings.market_timezone,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("종료 요청 수신")


def main() -> None:
    parser = argparse.ArgumentParser(description="뉴스 기반 호재 탐지 파이프라인")
    parser.add_argument("--once", action="store_true", help="한 번만 실행하고 종료")
    parser.add_argument(
        "--force", action="store_true", help="장 시간이 아니어도 강제로 실행 (테스트용)"
    )
    parser.add_argument(
        "--backend",
        choices=["ollama", "openvino"],
        default=None,
        help="1차 필터 백엔드를 config.yaml의 local_filter.backend 대신 일회성으로 지정",
    )
    parser.add_argument(
        "--device",
        choices=["CPU", "GPU", "NPU", "AUTO"],
        default=None,
        help="--backend openvino일 때 사용할 디바이스 (기본: config.yaml의 openvino.device)",
    )
    parser.add_argument(
        "--model-path",
        default=None,
        help="--backend openvino일 때 사용할 모델 경로 (기본: config.yaml의 openvino.model_path)",
    )
    args = parser.parse_args()

    # config.yaml을 고치지 않고도 이 실행 한 번에 한해 백엔드/디바이스/모델을 바꿀 수 있게 함.
    if args.backend:
        settings.local_filter_backend = args.backend
    if args.device:
        settings.openvino_device = args.device
    if args.model_path:
        model_path = Path(args.model_path)
        settings.openvino_model_path = (
            model_path if model_path.is_absolute() else settings.root / model_path
        )

    setup_logging()
    pipeline = NewsPipeline()

    try:
        if args.once:
            if not args.force and not is_market_open():
                logger.warning("현재 장 시간이 아닙니다. --force 옵션으로 강제 실행할 수 있습니다.")
                return
            records = pipeline.run_once()
            logger.info("1회 실행 완료 - %d건 처리", len(records))
        else:
            run_forever(pipeline, force=args.force)
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()
